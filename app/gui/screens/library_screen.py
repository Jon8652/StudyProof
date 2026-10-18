"""PDF Study Library screen (Phase 2.3/2.4, UI-20 / LIB-1..6 / PDF-1..7).

Split pane: document list on the left (file name, page count, imported
date, status chip Ready/Textless/Extracting/Missing) and the PageViewer on
the right.  All heavy lifting goes through AppState (records + binaries)
and app.core.pdfx via a background ExtractionWorker — the main thread only
ever receives posted events through the shared UIEventQueue (A-30).

Edge cases handled here (Phase 2.4):
- >100 MB refusals happen in AppState.import_document before any copy
  (ERR-30); >2,000-page refusals come back from the worker as ``too_large``
  and the record + binary are rolled back (PDF-2/PDF-5).
- Encrypted/corrupt files → friendly ERR-29 message; failed imports never
  leave a record behind.
- Zero-text PDFs land in ``textless`` (ERR-31): viewer works, selection
  lists them as unavailable (handled by AppState.selectable_sources).
- Missing binaries at startup show ``missing`` with Re-link… / Remove
  (ERR-32/LIB-6); re-link verifies page count before adopting the file.
- Duplicates by (name, size) are refused with an "already in your library"
  notice plus an *Open existing* button (PDF-3).
"""

from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from app.constants import MAX_PDF_BYTES, ZOOM_LEVELS
from app.core.models import Document
from app.gui.dialogs.confirm_dialog import ask_yes_no
from app.gui.dialogs.message_dialog import show_error, show_message
from app.gui.screens.base_screen import BaseScreen
from app.gui.theme import ACCENT, CARD_PAD_X, MUTED_TEXT, danger_button, neutral_button
from app.gui.widgets.page_viewer import PageViewer
from app.gui.workers import ExtractionWorker, UIEventQueue
from app.persistence import library
from app.persistence.schema import FRIENDLY_MESSAGES

# Status chip colours: (light, dark) per DOC_STATUSES.  Text always names
# the state too, so chips never rely on colour alone (ACC-3 spirit).
STATUS_CHIP_COLORS = {
    "ready": ("#d8ecd8", "#25402a"),
    "textless": ("#f2e6c8", "#453a22"),
    "extracting": ("#dbe7f5", "#22304a"),
    "missing": ("#f2d4d4", "#472323"),
}
STATUS_LABELS = {
    "ready": "Ready", "textless": "Textless",
    "extracting": "Extracting", "missing": "Missing",
}

_IMPORT_ERROR_MESSAGES = {
    "encrypted": "This PDF couldn't be opened (it may be protected or "
                 "damaged).",
    "unreadable": "This PDF couldn't be opened (it may be protected or "
                  "damaged).",
    "too_large": FRIENDLY_MESSAGES["pdf_too_large"],
    "missing": "The stored copy of this PDF can't be found in the library "
               "folder. Use \u201cRe-link file\u2026\u201d to point at it again.",
    "dependency_missing":
        "StudyProof's PDF engine (PyMuPDF) is not installed in this "
        "environment, so no PDF can be opened. Run "
        "\u201cpip install -r requirements.txt\u201d and restart the app.",
}


class LibraryScreen(BaseScreen):
    def __init__(self, master, state):
        super().__init__(master, state, title="Library")

        # One shared event pump for every worker this screen owns (A-30).
        self._events = UIEventQueue(master.winfo_toplevel())
        self._events.start()
        self._worker = ExtractionWorker(
            self._events,
            on_progress=self._on_extract_progress,
            on_done=self._on_extract_done,
            on_failed=self._on_extract_failed)

        self._row_frames: dict[str, ctk.CTkFrame] = {}
        self._row_widgets: dict[str, dict[str, object]] = {}
        self._selected_id: str | None = None
        self._progress_text: dict[str, str] = {}

        body = ctk.CTkFrame(self.content, fg_color="transparent")
        body.pack(fill="both", expand=True)
        # Pane ratio ≈ 1:2 — the list pane gets a fixed comfortable width
        # (roomy enough for the empty-state text to wrap over a few relaxed
        # lines) while the viewer takes the remaining ~2/3 of the screen.
        body.grid_columnconfigure(0, weight=0, minsize=380)
        body.grid_columnconfigure(1, weight=2)
        body.grid_rowconfigure(0, weight=1)

        # --- left: import button + document list -----------------------------
        left = ctk.CTkFrame(body, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, CARD_PAD_X),
                  pady=0)

        self.import_button = ctk.CTkButton(
            left, text="+ Import PDF", height=36, command=self._import_clicked)
        self.import_button.pack(fill="x", pady=(0, 8))

        self.list_scroll = ctk.CTkScrollableFrame(
            left, fg_color="transparent")
        self.list_scroll.pack(fill="both", expand=True)
        self.list_scroll.grid_columnconfigure(0, weight=1)

        self.empty_label = ctk.CTkLabel(
            self.list_scroll,
            text="Your library is empty.\n\nImport a PDF and StudyProof "
                 "copies it into its own folder, then extracts the text "
                 "page by page in the background.",
            font=ctk.CTkFont(size=13), text_color=MUTED_TEXT,
            wraplength=340, justify="left")
        self.empty_label.pack(fill="x", padx=4, pady=8)

        # --- right: viewer + per-document actions ------------------------------
        right = ctk.CTkFrame(body, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_rowconfigure(0, weight=1)
        right.grid_columnconfigure(0, weight=1)

        self.viewer = PageViewer(right, self._events)
        self.viewer.grid(row=0, column=0, sticky="nsew")

        self.action_bar = ctk.CTkFrame(right, fg_color="transparent")
        self.action_bar.grid(row=1, column=0, sticky="ew", pady=(6, 2))

        self.relink_button = neutral_button(
            self.action_bar, text="Re-link file…", width=110,
            command=self._relink_clicked)
        self.relink_button.configure(state="disabled")
        self.relink_button.pack(side="left")

        self.extract_button = neutral_button(
            self.action_bar, text="Re-extract", width=100,
            command=self._reextract_clicked)
        self.extract_button.configure(state="disabled")
        self.extract_button.pack(side="left", padx=6)

        self.external_button = neutral_button(
            self.action_bar, text="Open externally", width=120,
            command=self._open_external_clicked)
        self.external_button.configure(state="disabled")
        self.external_button.pack(side="left")

        self.cancel_extract_button = neutral_button(
            self.action_bar, text="Cancel extraction", width=130,
            command=self._cancel_extract_clicked)
        self.cancel_extract_button.pack_forget()

        self.delete_button = danger_button(
            self.action_bar, text="Delete", width=90,
            command=self._delete_clicked)
        self.delete_button.configure(state="disabled")
        self.delete_button.pack(side="right")

        self.status_line = ctk.CTkLabel(
            right, text="", font=ctk.CTkFont(size=12), text_color=MUTED_TEXT,
            anchor="w", wraplength=520, justify="left")
        self.status_line.grid(row=2, column=0, sticky="ew")

        self.refresh()
        # Resume anything that was mid-extraction when the app closed.
        self._schedule_pending_extractions()

    # ------------------------------------------------------------------------
    # Import flow (PDF-1..PDF-3, ERR-29/ERR-30)

    def _import_clicked(self) -> None:
        path_str = filedialog.askopenfilename(
            title="Import PDF",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")])
        if not path_str:
            return
        self._import_path(Path(path_str))

    def _import_path(self, path: Path) -> bool:
        """Run the full import pipeline for one chosen file; True on accept."""
        try:
            size = path.stat().st_size
        except OSError:
            show_error(self, FRIENDLY_MESSAGES["unreadable"])
            return False
        if size > MAX_PDF_BYTES:                      # ERR-30 pre-copy cap
            show_message(self, "PDF too large",
                         FRIENDLY_MESSAGES["pdf_too_large"])
            return False

        # Probe with PyMuPDF *before* copying so we can give an accurate,
        # friendly reason for any refusal — and offer a password prompt for
        # protected files instead of the generic "couldn't be opened".
        decision = self._precheck_pdf(path)
        if decision == "rejected":
            return False        # message already shown by _precheck_pdf
        if decision == "cancelled":
            return False        # user backed out of the password dialog

        result = self.state.import_document(path, file_size=size,
                                            verified=True)
        if not result.ok:
            if result.message == FRIENDLY_MESSAGES["duplicate_import"]:
                self._offer_open_existing(result.value, path.name)
            else:                                      # ERR-29 friendly text
                show_message(self, "Couldn't import",
                             result.message or "")
            return False

        document = result.value
        self._selected_id = document.id
        self.refresh()
        self._start_extraction(document.id)
        return True

    def _precheck_pdf(self, path: Path) -> str:
        """Open ``path`` with PyMuPDF; returns accepted/rejected/cancelled.

        Handles every ERR-29 kind explicitly (encrypted / unreadable /
        missing) and retries once with a user-supplied password when the
        file is owner-password protected.  A successfully opened document
        is closed here; the caller re-verifies cheaply downstream.
        """
        from app.core import pdfx
        from app.core.pdfx import PdfOpenError
        try:
            pymupdf = pdfx._load_pymupdf()
        except ImportError:
            show_message(self, "PDF engine missing",
                         _IMPORT_ERROR_MESSAGES["dependency_missing"])
            return "rejected"
        try:
            doc = pymupdf.open(str(path))
        except Exception:
            show_message(self, "Couldn't open PDF",
                         _IMPORT_ERROR_MESSAGES["unreadable"])
            return "rejected"
        if doc.needs_pass:
            # An empty password unlocks many "protected" PDFs (permissions-
            # only encryption); only ask the user when that fails.
            unlocked = False
            try:
                unlocked = bool(doc.authenticate(""))
            except Exception:
                unlocked = False
            if not unlocked:
                password = self._ask_password(path.name)
                if password is None:               # dialog cancelled
                    doc.close()
                    return "cancelled"
                try:
                    unlocked = bool(doc.authenticate(password))
                except Exception:
                    unlocked = False
                if not unlocked:
                    doc.close()
                    show_message(self, "Wrong password",
                                 "StudyProof couldn't unlock that PDF with "
                                 "the password you entered.")
                    return "rejected"
        try:
            if doc.page_count <= 0:
                show_message(self, "Couldn't open PDF",
                             _IMPORT_ERROR_MESSAGES["unreadable"])
                return "rejected"
            return "accepted"
        finally:
            doc.close()

    def _ask_password(self, file_name: str) -> "str | None":
        """Small modal password prompt; returns None when cancelled."""
        dialog = ctk.CTkToplevel(self.winfo_toplevel())
        dialog.title("Protected PDF")
        dialog.transient(self.winfo_toplevel())
        dialog.resizable(False, False)
        frame = ctk.CTkFrame(dialog, corner_radius=12)
        frame.pack(fill="both", expand=True, padx=16, pady=16)
        ctk.CTkLabel(frame, text="Password required",
                     font=ctk.CTkFont(size=15, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(
            frame, wraplength=320, justify="left",
            text=f'"{file_name}" is password-protected. Enter the password '
                 "so StudyProof can read it.",
            text_color=MUTED_TEXT).pack(anchor="w", pady=(6, 10))
        entry = ctk.CTkEntry(frame, show="•", width=280)
        entry.pack(anchor="w")
        result: dict[str, "str | None"] = {"value": None}

        def _ok():
            result["value"] = entry.get()
            dialog.destroy()

        def _cancel():
            result["value"] = None
            dialog.destroy()

        buttons = ctk.CTkFrame(frame, fg_color="transparent")
        buttons.pack(anchor="e", pady=(10, 0))
        neutral_button(buttons, text="Cancel", command=_cancel).pack(side="right",
                                                                    padx=(8, 0))
        ctk.CTkButton(buttons, text="Unlock", command=_ok).pack(side="right")
        entry.bind("<Return>", lambda _e: _ok())
        dialog.grab_set()
        self.winfo_toplevel().wait_window(dialog)
        return result["value"]

    def _offer_open_existing(self, existing_id: str | None,
                             file_name: str) -> None:
        """PDF-3: refuse duplicates but offer to jump to the stored record."""
        dialog = ctk.CTkToplevel(self.winfo_toplevel())
        dialog.title("Already in your library")
        dialog.transient(self.winfo_toplevel())
        dialog.resizable(False, False)
        frame = ctk.CTkFrame(dialog, corner_radius=12)
        frame.pack(fill="both", expand=True, padx=16, pady=16)
        ctk.CTkLabel(frame, text="Duplicate PDF",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(0, 8))
        ctk.CTkLabel(
            frame,
            text=f"{file_name} is already in your library — no second copy "
                 "was made.",
            wraplength=360, justify="left", font=ctk.CTkFont(size=13)).pack(
            pady=(0, 14))
        buttons = ctk.CTkFrame(frame, fg_color="transparent")
        buttons.pack()

        def _close():
            dialog.grab_release()
            dialog.destroy()

        def _open():
            _close()
            if existing_id:
                self.select_document(existing_id)

        ctk.CTkButton(buttons, text="Close", width=100,
                      fg_color=("gray30", "gray25"),
                      command=_close).pack(side="left", padx=6)
        if existing_id:
            ctk.CTkButton(buttons, text="Open existing", width=120,
                          command=_open).pack(side="left", padx=6)
        dialog.grab_set()
        dialog.protocol("WM_DELETE_WINDOW", _close)

    # ------------------------------------------------------------------------
    # Background extraction (PDF-4, A-30)

    def _start_extraction(self, document_id: str) -> None:
        document = self.state.document_by_id(document_id)
        if document is None:
            return
        path = library.binary_path(document.stored_name)
        self.state.update_document_status(document_id, "extracting",
                                         save=False)
        self._worker.start(document_id, path)
        self._refresh_rows()

    def _schedule_pending_extractions(self) -> None:
        for document_id in self.state.pending_extraction_ids():
            self._start_extraction(document_id)

    def _on_extract_progress(self, document_id: str, page_no: int,
                             total: int) -> None:
        self._progress_text[document_id] = \
            f"Extracting page {page_no} of {total}"
        row = self._row_widgets.get(document_id)
        if row is not None and self._selected_id == document_id:
            pass  # status line handled below
        if document_id == self._selected_id:
            self._update_status_line()
        elif row is not None:
            meta = row.get("meta")
            if isinstance(meta, ctk.CTkLabel):
                meta.configure(text=self._progress_text[document_id])

    def _on_extract_done(self, document_id: str, result) -> None:
        self._progress_text.pop(document_id, None)
        pages = [(no, text) for no, text in result.pages]
        # Empty status string = "derive ready/textless from the new text".
        self.state.update_document_status(document_id, "", pages=pages)
        self.refresh()

    def _on_extract_failed(self, document_id: str, kind: str) -> None:
        self._progress_text.pop(document_id, None)
        if kind == "cancelled":
            document = self.state.document_by_id(document_id)
            if document is not None:
                # Keep whatever text exists; mark honestly.
                self.state.update_document_status(
                    document_id,
                    "ready" if document.char_count > 0 else "textless")
            self._update_status_line()
            return
        if kind == "too_large":
            # PDF-2/PDF-5: roll back the whole import — record + binary.
            self.state.delete_document(document_id)
            show_message(self, "PDF too large",
                         _IMPORT_ERROR_MESSAGES["too_large"])
            self._selected_id = None
            self.refresh()
            return
        # ERR-29: encrypted/corrupt after copy — remove partial record/binary.
        self.state.delete_document(document_id)
        show_message(self, "Couldn't open PDF",
                     _IMPORT_ERROR_MESSAGES.get(kind,
                                                _IMPORT_ERROR_MESSAGES[
                                                    "unreadable"]))
        self._selected_id = None
        self.refresh()

    def _cancel_extract_clicked(self) -> None:
        if self._selected_id:
            self._worker.cancel(self._selected_id)

    def _reextract_clicked(self) -> None:
        # LIB-4: replace stored text directly — no confirmation needed.
        if self._selected_id:
            self._start_extraction(self._selected_id)

    # ------------------------------------------------------------------------
    # Row selection / actions

    def select_document(self, document_id: str) -> None:
        self._selected_id = document_id
        self._refresh_rows()
        self._sync_viewer()

    def _delete_clicked(self) -> None:
        document = self._current_document()
        if document is None:
            return
        confirmed = ask_yes_no(
            self.winfo_toplevel(), "Delete document",
            f"Remove “{document.display_label()}” from your library? The "
            "stored copy and its extracted text will be deleted. Coach "
            "sessions keep their history.",
            confirm_text="Delete")
        if not confirmed:
            return
        result = self.state.delete_document(document.id)
        if not result.ok:
            show_error(self, result.message or "")
            return
        self._selected_id = None
        self.refresh()

    def _relink_clicked(self) -> None:
        document = self._current_document()
        if document is None:
            return
        path_str = filedialog.askopenfilename(
            title=f"Pick the file for {document.display_label()}",
            filetypes=[("PDF files", "*.pdf")])
        if not path_str:
            return
        from app.core import pdfx
        try:
            pages = pdfx.get_page_count(path_str)
        except pdfx.PdfOpenError as exc:
            show_message(self, "Couldn't open PDF",
                         _IMPORT_ERROR_MESSAGES.get(exc.kind,
                                                    _IMPORT_ERROR_MESSAGES[
                                                        "unreadable"]))
            return
        expected = document.page_count
        if expected and pages != expected:
            show_message(
                self, "Not the same file",
                f"That PDF has {pages} page(s), but this record expects "
                f"{expected}. Please pick the original file.")
            return
        result = self.state.relink_document(document.id, path_str)
        if not result.ok:
            show_error(self, result.message or "")
            return
        # Binary is back: if its stored text was still incomplete, refresh it.
        if document.char_count == 0:
            self._start_extraction(document.id)
        self.refresh()

    def _open_external_clicked(self) -> None:
        document = self._current_document()
        if document is None:
            return
        path = library.binary_path(document.stored_name)
        if not path.is_file():
            show_message(self, "File missing",
                         "The stored copy of this PDF can't be found. Use "
                         "“Re-link file…” to point at it again.")
            return
        try:
            if sys.platform.startswith("win"):
                import os
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception:
            show_error(self, "Your system couldn't open that file "
                             "automatically.")

    def _current_document(self) -> Document | None:
        if self._selected_id is None:
            return None
        return self.state.document_by_id(self._selected_id)

    # ------------------------------------------------------------------------
    # Rendering

    def refresh(self) -> None:
        self._refresh_rows()
        self._sync_viewer()

    def _refresh_rows(self) -> None:
        documents = self.state.get_documents()
        live_ids = {d.id for d in documents}
        for stale in [doc_id for doc_id in self._row_frames
                      if doc_id not in live_ids]:
            self._row_frames[stale].destroy()
            del self._row_frames[stale]
            self._row_widgets.pop(stale, None)

        if not documents:
            self.empty_label.pack(fill="x", pady=12)
        else:
            self.empty_label.pack_forget()

        for index, document in enumerate(documents):
            row = self._row_frames.get(document.id)
            if row is None:
                row = self._build_row(document)
                self._row_frames[document.id] = row
            row.grid(row=index, column=0, sticky="ew", pady=2)
            self._update_row(document)

    def _build_row(self, document: Document) -> ctk.CTkFrame:
        row = ctk.CTkFrame(self.list_scroll, corner_radius=10,
                           fg_color=("gray90", "#232331"), border_width=1,
                           border_color=("gray75", "gray28"))
        name = ctk.CTkLabel(row, text=document.display_label(),
                            font=ctk.CTkFont(size=13, weight="bold"),
                            anchor="w", wraplength=280)
        name.pack(fill="x", padx=10, pady=(8, 0))
        meta = ctk.CTkLabel(row, text="", font=ctk.CTkFont(size=11),
                            text_color=MUTED_TEXT, anchor="w")
        meta.pack(fill="x", padx=10)
        chip = ctk.CTkLabel(row, text="", corner_radius=6,
                            font=ctk.CTkFont(size=11, weight="bold"),
                            padx=8, pady=2)
        chip.pack(anchor="w", padx=10, pady=(0, 8))
        row.bind("<Button-1>", lambda _e, d=document.id:
                 self.select_document(d))
        for widget in (name, meta, chip):
            widget.bind("<Button-1>", lambda _e, d=document.id:
                        self.select_document(d))
        self._row_widgets[document.id] = {"row": row, "name": name,
                                           "meta": meta, "chip": chip}
        return row

    def _update_row(self, document: Document) -> None:
        widgets = self._row_widgets.get(document.id)
        if widgets is None:
            return
        name = widgets["name"]
        meta = widgets["meta"]
        chip = widgets["chip"]
        name.configure(text=document.display_label())
        progress = self._progress_text.get(document.id)
        imported = document.imported_at_local.date()
        parts = []
        if progress:
            parts.append(progress)
        else:
            parts.append(f"{document.page_count} pages")
            parts.append(_format_imported(imported))
        meta.configure(text=" · ".join(parts))
        chip.configure(text=STATUS_LABELS.get(document.status,
                                              document.status),
                       fg_color=STATUS_CHIP_COLORS.get(
                           document.status, ("gray80", "gray28")))
        selected = document.id == self._selected_id
        widgets["row"].configure(
            border_color=ACCENT if selected else ("gray75", "gray28"),
            fg_color=("#dbe7f5", "#2b3a52") if selected
            else ("gray90", "#232331"))

    def _sync_viewer(self) -> None:
        document = self._current_document()
        if document is None:
            self.viewer.set_document(None, None, 0, {})
            for button in (self.relink_button, self.extract_button,
                           self.external_button, self.delete_button):
                button.configure(state="disabled")
            self.status_line.configure(text="")
            return

        binary_present = library.binary_path(document.stored_name).is_file()
        text_map = {int(p["page"]): p["text"] for p in document.text_pages}
        if document.status == "missing" or not binary_present:
            # LIB-6/ERR-32: viewer disabled, stored text still browsable.
            self.viewer.set_document(document.id, None,
                                     max(document.page_count,
                                         len(text_map)), text_map)
            self.viewer._fallback_to_text(
                "The stored file is missing, so page images can't be shown. "
                "Saved text still works — use “Re-link file…” below.")
        else:
            self.viewer.set_document(document.id,
                                     library.binary_path(
                                         document.stored_name),
                                     max(document.page_count, 1), text_map)
        self.relink_button.configure(
            state="normal" if not binary_present else "disabled")
        self.extract_button.configure(
            state="normal" if binary_present else "disabled")
        self.external_button.configure(
            state="normal" if binary_present else "disabled")
        self.delete_button.configure(state="normal")
        self._update_status_line()

    def _update_status_line(self) -> None:
        document = self._current_document()
        if document is None:
            self.status_line.configure(text="")
            self.cancel_extract_button.pack_forget()
            return
        progress = self._progress_text.get(document.id)
        extracting = document.status == "extracting"
        if extracting or progress:
            self.cancel_extract_button.pack(side="right")
        else:
            self.cancel_extract_button.pack_forget()
        bits = [f"{document.char_count:,} characters stored"]
        if progress:
            bits.insert(0, progress)
        elif extracting:
            bits.insert(0, "Extracting…")
        self.status_line.configure(text=" · ".join(bits))

    def shutdown(self) -> None:
        """Stop the event pump (called from MainWindow._on_close)."""
        self._events.stop()


def _format_imported(day: date) -> str:
    today = date.today()
    if day == today:
        return "imported today"
    delta = (today - day).days
    if delta == 1:
        return "imported yesterday"
    if delta < 30:
        return f"imported {delta} days ago"
    return day.strftime("imported %d %b %Y")
