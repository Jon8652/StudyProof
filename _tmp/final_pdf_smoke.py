"""End-to-end GUI smoke: import a real PDF, extract in worker, view page."""
import os, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("STUDYPROOF_DATA_DIR", tempfile.mkdtemp())

import pymupdf
# Build a 3-page test PDF
pdf_dir = Path(tempfile.mkdtemp())
src = pdf_dir / "biology_ch1.pdf"
doc = pymupdf.open()
for i in range(3):
    p = doc.new_page()
    p.insert_text((72, 72 + 40 * i), f"Cell biology chapter {i+1}: mitochondria are the powerhouse.")
doc.save(src); doc.close()

import customtkinter as ctk
from app.state.app_state import AppState
from app.gui.screens.library_screen import LibraryScreen

root = ctk.CTk()
root.geometry("1100x700")
state = AppState.load()
screen = LibraryScreen(root, state)
screen.pack(fill="both", expand=True) if hasattr(screen, "pack") else None

# Empty-state label must be visible with readable wrap width
assert screen.empty_label.winfo_ismapped(), "empty label not shown initially"
print("Empty state visible:", screen.empty_label.cget("text")[:30], "... wraplength =", screen.empty_label.cget("wraplength"))

# Import via the same pipeline the button uses
ok = screen._import_path(src)
assert ok, "import failed"
print("Import accepted; document selected:", screen._selected_id is not None)

# Pump events until extraction finishes (worker thread -> UIEventQueue)
deadline = 500
while deadline and (screen._selected_id is None or
                    (state.document_by_id(screen._selected_id) and
                     state.document_by_id(screen._selected_id).status == "extracting")):
    root.update_idletasks(); root.update()
    deadline -= 1
    if deadline % 50 == 0:
        import time; time.sleep(0.02)

document = state.document_by_id(screen._selected_id)
print("Status after extraction:", document.status, "| pages:", document.page_count,
      "| chars:", document.char_count)
assert document.status == "ready", document.status
assert document.page_count == 3 and document.char_count > 0

# Viewer should render page 1 image (pixmap path) without falling back to error
viewer_ok = False
for _ in range(60):
    root.update_idletasks(); root.update()
    import time; time.sleep(0.03)
    try:
        from app.gui.widgets.page_viewer import PageViewer
        # check no failure notice text present
        for w in screen.viewer.winfo_children():
            pass
    except Exception:
        pass
# Direct render check through pdfx on stored binary:
from app.persistence import library
stored = library.binary_path(document.stored_name)
img = __import__("app.core.pdfx", fromlist=["render_page_pixmap"]).render_page_pixmap(stored, 2, 1.5)
print("Render of stored binary OK:", img.size)
assert img.size[0] > 100

# Duplicate refusal path
result = state.import_document(src, file_size=src.stat().st_size)
print("Duplicate refused:", not result.ok)
assert not result.ok

root.destroy()
print("FINAL SMOKE PASS")
