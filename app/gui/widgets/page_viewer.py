"""Page viewer widget for the Library screen (Phase 2.3, UI-20/LIB-2/3).

Displays one PDF page at a time: rendered pixmap (zoom 1.0 / 1.5 / 2.0),
prev/next navigation with "Page i of n", and a toggle to the stored text
pane.  Rendering goes through ``app.core.pdfx`` off the main thread;
results come back via the shared ``UIEventQueue`` so the Tk thread is the
only one touching widgets (A-30).  Rendered pages are kept in a strict
5-entry LRU cache (LIB-3).

Fallback rule (LIB-3): if rendering fails for any reason, the viewer
switches to the text pane with a friendly notice — it never crashes.

Image lifetime (the "pyimageN doesn't exist" family of bugs): page images
are shown by :class:`app.gui.widgets.page_image.PageImageLabel`, a plain
tkinter label that keeps its own strong references to every PhotoImage it
creates and clears the label with an empty string (never ``None``) before
swapping.  CTkImage objects are no longer handed to a CTkLabel whose async
redraws used to hit dead image names during document switches, zoom
changes, and page navigation.
"""

from __future__ import annotations

import threading
import tkinter as tk
from collections import OrderedDict
from pathlib import Path

import customtkinter as ctk

from app.constants import PAGE_RENDER_CACHE_SIZE, ZOOM_LEVELS
from app.gui.theme import MUTED_TEXT
from app.gui.widgets.page_image import PageImageLabel, theme_is_dark
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
        # Cache holds PIL images; everything evicted or retired moves to
        # _retired_images so nothing Tk may still reference is ever freed
        # before this widget dies.
        self._cache: "OrderedDict[tuple[int, float], object]" = OrderedDict()
        self._retired_images: list = []

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
        # Image stage: a plain tk canvas + label.  We deliberately avoid
        # CTkScrollableFrame here: its canvas redraw re-configures every
        # child widget, which is exactly what made Tk try to re-resolve a
        # stale pyimage name ("image 'pyimageN' doesn't exist") during page
        # switches and zoom changes.  The canvas gives us native scrolling
        # for tall/zoomed pages without any of that redraw coupling.
        self.image_canvas = tk.Canvas(self, highlightthickness=0, bd=0,
                                      bg="#1c1c26")
        self._image_vbar = ctk.CTkScrollbar(self, command=self.image_canvas.yview)
        self.image_canvas.configure(yscrollcommand=self._image_vbar.set)
        self.image_label = PageImageLabel(self.image_canvas)
        self._image_window = self.image_canvas.create_window(
            (0, 0), window=self.image_label, anchor="nw")

        def _sync_scrollregion(_event=None):
            self.image_canvas.configure(
                scrollregion=self.image_canvas.bbox("all") or (0, 0, 0, 0))

        def _fit_width(event):
            # Keep the label as wide as the canvas so text placeholders wrap
            # sensibly, while taller images drive the scroll region.
            self.image_canvas.itemconfigure(self._image_window,
                                            width=max(event.width - 4, 1))

        self.image_label.bind("<Configure>", _sync_scrollregion)
        self.image_canvas.bind("<Configure>", _fit_width)
        # Scroll wheel support for Windows/macOS (delta) and Linux (button-4/5).
        self.image_canvas.bind_all(
            "<MouseWheel>",
            lambda e: self.image_canvas.yview_scroll_units(
                -1 if getattr(e, "delta", 0) > 0 else 1, "units"))
        self.image_canvas.bind_all("<Button-4>",
                                   lambda e: self.image_canvas.yview_scroll_units(-1, "units"))
        self.image_canvas.bind_all("<Button-5>",
                                   lambda e: self.image_canvas.yview_scroll_units(1, "units"))
        self.apply_appearance(theme_is_dark(self))

        # Text stage: also created up-front but only packed in text mode.
        self.text_scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.text_box = ctk.CTkTextbox(
            self.text_scroll, wrap="word", font=ctk.CTkFont(size=13))
        self.text_box.pack(fill="both", expand=True, padx=4, pady=4)

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
            # Retire (never drop) cached images so Tk keeps valid pyimage
            # references; see _retired_images.
            self._retire_cache()
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
        self._retire_cache()         # cached images are per-zoom
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
        self._show_stage("image")
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
            # doc_id is captured only to detect documents that were swapped
            # out while this render was in flight (belt-and-braces alongside
            # the seq guard); it must never be deleted afterwards.
            self._events.post(
                lambda img=image, p=page_no, z=zoom, d=doc_id:
                self._on_rendered(seq, img, p, z, d))

        threading.Thread(target=worker, daemon=True,
                         name="pdf-render").start()
        self._set_placeholder(f"Rendering page {page_no}…")

    def _retire_cache(self) -> None:
        """Move all cached images to the retire list (never free them).

        Dropping an image while Tk still holds its pyimageN causes
        ``TclError: image "pyimageN" doesn't exist`` on the next label
        redraw, which is what made zoom/document-switch go blank.
        """
        for image in self._cache.values():
            self._retired_images.append(image)
        self._cache.clear()

    def _on_rendered(self, seq: int, pil_image, page_no: int,
                     zoom: float, doc_id: str | None = None) -> None:
        if seq != self._render_seq or page_no != self._page_number:
            return  # stale result from a superseded request
        if doc_id is not None and doc_id != self._document_id:
            return  # document was switched while the render was in flight
        key = (page_no, zoom)
        self._cache[key] = pil_image
        while len(self._cache) > PAGE_RENDER_CACHE_SIZE:
            _evicted_key, evicted = self._cache.popitem(last=False)
            # Keep the evicted image alive until the widget dies; freeing it
            # early is exactly what triggers the pyimage TclError.
            self._retired_images.append(evicted)
        self._display_image(pil_image)

    def _on_render_failed(self, seq: int) -> None:
        if seq != self._render_seq:
            return
        self._fallback_to_text("This page couldn't be rendered — showing its "
                               "stored text instead.")

    def _display_image(self, pil_image) -> None:
        self._show_stage("image")
        self.image_label.set_image(pil_image)

    def _fallback_to_text(self, notice: str) -> None:
        self._show_text = True
        self.text_toggle.configure(text="Show image")
        self._set_placeholder(notice)
        self._render_text_pane()

    def _render_text_pane(self) -> None:
        self._show_stage("text")
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

    def _show_stage(self, stage: str) -> None:
        """Pack exactly one of the two stages (image / text)."""
        if stage == "image":
            self.text_scroll.pack_forget()
            if not self._is_packed(self.image_canvas):
                self.image_canvas.pack(side="left", fill="both", expand=True,
                                       padx=(8, 0), pady=8)
                self._image_vbar.pack(side="right", fill="y", padx=(2, 8),
                                      pady=8)
        else:
            self.image_canvas.pack_forget()
            self._image_vbar.pack_forget()
            if not self._is_packed(self.text_scroll):
                self.text_scroll.pack(fill="both", expand=True,
                                      padx=8, pady=8)

    @staticmethod
    def _is_packed(widget) -> bool:
        try:
            return bool(widget.winfo_manager())
        except Exception:
            return False

    def _set_placeholder(self, message: str) -> None:
        self._show_stage("image")
        self.image_label.set_message(message)

    def apply_appearance(self, is_dark: bool) -> None:
        self.image_label.apply_appearance(is_dark)

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
