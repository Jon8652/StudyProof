"""Page viewer widget for the Library screen (Phase 2.3, UI-20/LIB-2/3).

Displays one PDF page at a time: rendered pixmap (zoom 1.0 / 1.5 / 2.0),
prev/next navigation with "Page i of n", and a toggle to the stored text
pane.  Rendering goes through ``app.core.pdfx`` off the main thread when a
worker is available; results come back via the shared ``UIEventQueue``.
Rendered images are kept in a strict 5-page LRU cache (A-30/LIB-3).

Fallback rule (LIB-3): if rendering fails for any reason, the viewer
switches to the text pane with a friendly notice — it never crashes.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from pathlib import Path

import customtkinter as ctk

from app.constants import PAGE_RENDER_CACHE_SIZE, ZOOM_LEVELS
from app.gui.theme import ACCENT, MUTED_TEXT
from app.gui.workers import UIEventQueue


class PageViewer(ctk.CTkFrame):
    def __init__(self, master, events: UIEventQueue):
        super().__init__(master, corner_radius=12, fg_color=("white", "#1c1c26"),
                         border_width=1, border_color=("gray75", "gray28"))
        self._events = events
        self._document_id: str | None = None
        self._path: Path | None = None
        self._page_number = 1
        self._page_count = 0
        self._zoom_index = 0            # index into ZOOM_LEVELS
        self._text_by_page: dict[int, str] = {}
        self._show_text = False
        self._render_seq = 0            # drop stale async render results
        self._cache: "OrderedDict[tuple[int, float], object]" = OrderedDict()
        self._image_ref = None          # keep CTkImage alive

        # --- toolbar ---------------------------------------------------------
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=10, pady=(8, 2))

        self.prev_button = ctk.CTkButton(
            bar, text="‹ Prev", width=64,
            command=self._go_prev)
        self.prev_button.pack(side="left")
        self.page_label = ctk.CTkLabel(bar, text="No document",
                                       font=ctk.CTkFont(size=13))
        self.page_label.pack(side="left", padx=10)
        self.next_button = ctk.CTkButton(
            bar, text="Next ›", width=64,
            command=self._go_next)
        self.next_button.pack(side="left")

        self.zoom_button = ctk.CTkButton(
            bar, text=self._zoom_text(), width=70, fg_color=MUTED_TEXT[1],
            command=self._cycle_zoom)
        self.zoom_button.configure(fg_color=("gray80", "gray28"),
                                   text_color=("gray10", "gray90"))
        self.zoom_button.pack(side="right", padx=(6, 0))

        self.text_toggle = ctk.CTkButton(
            bar, text="Show text", width=90,
            command=self._toggle_text)
        self.text_toggle.pack(side="right")

        # --- stage ------------------------------------------------------------
        self.image_label = ctk.CTkLabel(self, text="", anchor="n")
        self.image_label.pack(fill="both", expand=True, padx=8, pady=8)

        # Hidden by default: created but never packed until text mode is on.
        self.text_scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.text_box = ctk.CTkTextbox(
            self.text_scroll, wrap="word", font=ctk.CTkFont(size=13))
        self.text_box.pack(fill="both", expand=True, padx=4, pady=4)

        self.notice_label = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=12), text_color=MUTED_TEXT,
            wraplength=420)

    # ------------------------------------------------------------------------
    # Public API (main thread only)

    def set_document(self, document_id: str | None, path: "str | Path | None",
                     page_count: int, text_pages: dict[int, str]) -> None:
        """Point the viewer at a document (or clear it when id is None)."""
        changed = document_id != self._document_id
        self._document_id = document_id
        self._path = Path(path) if path is not None else None
        self._page_count = max(0, int(page_count))
        self._text_by_page = dict(text_pages or {})
        if changed:
            self._cache.clear()
            self._page_number = 1
            self._render_seq += 1
        self._page_number = min(max(1, self._page_number),
                                max(1, self._page_count)) \
            if self._page_count else 1
        self.show_current()

    def update_text(self, text_pages: dict[int, str]) -> None:
        """Refresh stored text after extraction completes mid-view."""
        self._text_by_page = dict(text_pages or {})
        if self._show_text:
            self._render_text_pane()

    def show_current(self) -> None:
        if self._document_id is None:
            self._set_placeholder("Import or select a document to see it here.")
            return
        if self._page_count <= 0:
            self._set_placeholder("This document has no pages yet.")
            return
        self._update_nav()
        if self._show_text:
            self._render_text_pane()
        else:
            self._request_render()

    # ------------------------------------------------------------------------
    # Navigation / zoom / toggle

    def _go_prev(self) -> None:
        if self._page_number > 1:
            self._page_number -= 1
            self.show_current()

    def _go_next(self) -> None:
        if self._page_number < self._page_count:
            self._page_number += 1
            self.show_current()

    def _cycle_zoom(self) -> None:
        self._zoom_index = (self._zoom_index + 1) % len(ZOOM_LEVELS)
        self.zoom_button.configure(text=self._zoom_text())
        self._cache.clear()          # cached images are per-zoom
        self.show_current()

    def _toggle_text(self) -> None:
        self._show_text = not self._show_text
        self.text_toggle.configure(
            text="Show image" if self._show_text else "Show text")
        self.show_current()

    def _zoom_text(self) -> str:
        return f"{self.zoom:.1f}×"

    @property
    def zoom(self) -> float:
        return ZOOM_LEVELS[self._zoom_index]

    # ------------------------------------------------------------------------
    # Rendering (background thread + LRU cache, A-30/LIB-3)

    def _request_render(self) -> None:
        self.image_label.pack(fill="both", expand=True, padx=8, pady=8)
        self.text_scroll.pack_forget()
        key = (self._page_number, self.zoom)
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            self._display_image(cached)
            return
        if self._path is None:
            self._fallback_to_text("This file is missing on disk, so its "
                                   "page can't be rendered. Stored text is "
                                   "shown instead.")
            return
        assert self._document_id is not None
        seq = self._render_seq
        path, page_no, zoom = self._path, self._page_number, self.zoom
        doc_id = self._document_id

        def worker():
            from app.core import pdfx
            try:
                image = pdfx.render_page_pixmap(path, page_no, zoom)
            except Exception:
                self._events.post(lambda: self._on_render_failed(seq))
                return
            self._events.post(
                lambda img=image, p=page_no, z=zoom, d=doc_id:
                self._on_rendered(seq, img, p, z))
            # Touch doc-id reference so linters keep the closure honest.
            del d

        threading.Thread(target=worker, daemon=True,
                         name="pdf-render").start()
        self._set_placeholder(f"Rendering page {page_no}…")

    def _on_rendered(self, seq: int, pil_image, page_no: int,
                     zoom: float) -> None:
        if seq != self._render_seq or page_no != self._page_number:
            return  # stale result from a superseded request
        size = (pil_image.width, pil_image.height)
        ctk_image = ctk.CTkImage(light_image=pil_image,
                                 dark_image=pil_image, size=size)
        key = (page_no, zoom)
        self._cache[key] = ctk_image
        while len(self._cache) > PAGE_RENDER_CACHE_SIZE:
            self._cache.popitem(last=False)   # evict least-recently-used
        self._display_image(ctk_image)

    def _on_render_failed(self, seq: int) -> None:
        if seq != self._render_seq:
            return
        self._fallback_to_text("This page couldn't be rendered — showing its "
                               "stored text instead.")

    def _display_image(self, ctk_image) -> None:
        self._image_ref = ctk_image
        self.notice_label.pack_forget()
        self.text_scroll.pack_forget()
        self.image_label.configure(image=ctk_image, text="")
        self.image_label.pack(fill="both", expand=True, padx=8, pady=8)

    def _fallback_to_text(self, notice: str) -> None:
        self._show_text = True
        self.text_toggle.configure(text="Show image")
        self._set_placeholder(notice)
        self._render_text_pane()

    def _render_text_pane(self) -> None:
        self.image_label.configure(image=None, text="")
        self.image_label.pack_forget()
        self.text_scroll.pack(fill="both", expand=True, padx=8, pady=8)
        text = self._text_by_page.get(self._page_number, "")
        self.text_box.configure(state="normal")
        self.text_box.delete("1.0", "end")
        if text.strip():
            self.text_box.insert("1.0", text)
        else:
            self.text_box.insert(
                "1.0", "No stored text for this page.\n\n"
                       "(The PDF may be scanned/image-only — the page image "
                       "view still works.)")
        self.text_box.configure(state="disabled")

    # ------------------------------------------------------------------------

    def _set_placeholder(self, message: str) -> None:
        self._image_ref = None
        self.image_label.configure(image=None, text=message,
                                   text_color=MUTED_TEXT)
        self.image_label.pack(fill="both", expand=True, padx=8, pady=8)

    def _update_nav(self) -> None:
        self.page_label.configure(
            text=f"Page {self._page_number} of {self._page_count}")
        self.prev_button.configure(
            state="normal" if self._page_number > 1 else "disabled")
        self.next_button.configure(
            state="normal" if self._page_number < self._page_count
            else "disabled")
        disabled = self._document_id is None
        for button in (self.zoom_button, self.text_toggle):
            button.configure(state="disabled" if disabled else "normal")
