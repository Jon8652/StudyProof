"""PDF text extraction & page rendering via PyMuPDF (Phase 2.2).

ARCH-7: this is the *only* module that opens/parses library binaries. GUI
code asks for extracted pages or rendered pixmaps and never touches fitz
itself.  All functions here are thread-safe to call from a background
worker (they open and close their own documents); they must never be given
a Tk object.

Error kinds (mapped to friendly messages by the caller):
- ``encrypted``   — password-protected file (ERR-29)
- ``unreadable``  — corrupt / not a PDF at all (ERR-29)
- ``too_large``   — over the page cap after opening (PDF-2/ERR-30; the byte
  cap is enforced earlier, before any copy happens)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.constants import MAX_PAGE_CHARS, MAX_PDF_PAGES


def _open(path: "str | Path"):
    """Open a PDF for reading; raise PdfOpenError with an ERR-29 kind."""
    import pymupdf  # local import: keeps headless tests free of GUI deps

    try:
        doc = pymupdf.open(str(path))
    except Exception:
        raise PdfOpenError("unreadable")
    if doc.needs_pass:
        doc.close()
        raise PdfOpenError("encrypted")
    return doc


class PdfOpenError(Exception):
    """Friendly-kind error for files that cannot be opened/parsed."""

    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


@dataclass
class ExtractResult:
    pages: list[tuple[int, str]]   # (1-based page number, verbatim text)
    page_count: int
    char_count: int


def extract_document(path: "str | Path",
                     on_progress: Callable[[int, int], None] | None = None,
                     should_cancel: Callable[[], bool] | None = None
                     ) -> ExtractResult:
    """Extract per-page text (PDF-4/PDF-7).

    - Pages are numbered 1-based and text is stored verbatim UTF-8, clamped
      defensively at MAX_PAGE_CHARS per page (§14.1).
    - ``on_progress(page_no, total)`` is called once per page so the worker
      can publish "Extracting page i of n" (throttled by the caller).
    - ``should_cancel()`` is polled every page; returning True aborts with
      ExtractionCancelled (the caller restores the document to a sane state).
    """
    result = extract_pages(path, on_progress=on_progress,
                           should_cancel=should_cancel)
    return result


def extract_pages(path: "str | Path",
                  on_progress: Callable[[int, int], None] | None = None,
                  should_cancel: Callable[[], bool] | None = None
                  ) -> ExtractResult:
    import pymupdf  # noqa: F401  (kept for clarity; _open imports it)

    doc = _open(path)
    try:
        total = doc.page_count
        if total > MAX_PDF_PAGES:            # PDF-2: refuse over-cap files
            raise PdfOpenError("too_large")
        pages: list[tuple[int, int, str]] = []
        for index in range(total):
            if should_cancel is not None and should_cancel():
                raise ExtractionCancelled()
            page = doc.load_page(index)
            text = page.get_text("text") or ""
            text = text[:MAX_PAGE_CHARS]
            pages.append((index + 1, text))
            if on_progress is not None:
                on_progress(index + 1, total)
        pairs = [(no, text) for no, text in pages]
        return ExtractResult(pages=pairs, page_count=len(pairs),
                             char_count=sum(len(t) for _, t in pairs))
    finally:
        doc.close()


class ExtractionCancelled(Exception):
    """Raised inside the worker when the user cancels extraction (A-30)."""


def render_page_pixmap(path: "str | Path", page_number: int, zoom: float = 1.0):
    """Render one page to a PIL RGB image (LIB-2/LIB-3, NFR-14 ≤ 300 ms).

    ``page_number`` is 1-based.  Returns a ``PIL.Image.Image`` ready to be
    wrapped in a CTkImage on the main thread.  Raises PdfOpenError on any
    failure — callers fall back to the text pane with a friendly notice
    (LIB-3) and must never crash.
    """
    from PIL import Image
    import pymupdf

    doc = _open(path)
    try:
        index = int(page_number) - 1
        if index < 0 or index >= doc.page_count:
            raise PdfOpenError("unreadable")
        page = doc.load_page(index)
        matrix = pymupdf.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        return image
    finally:
        doc.close()


def get_page_count(path: "str | Path") -> int:
    """Cheap page-count probe used by re-link verification (LIB-6)."""
    doc = _open(path)
    try:
        return doc.page_count
    finally:
        doc.close()
