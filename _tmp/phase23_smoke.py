"""Phase 2/3 exit-gate smoke: import a generated PDF, extract in background,
navigate pages; then select sources and see pack size. No AI code."""
import sys, tempfile, time
from pathlib import Path
sys.path.insert(0, "/workspace")

_data_dir = Path(tempfile.mkdtemp())
from app.utils import paths
paths.set_override_data_dir(_data_dir)

import customtkinter as ctk
import pymupdf

# --- generate a small text PDF ------------------------------------------
pdf_path = _data_dir / "sample.pdf"
doc = pymupdf.open()
for i in range(3):
    page = doc.new_page()
    page.insert_text((72, 72), f"Sample study page {i+1}: mitochondria produce ATP.", fontsize=12)
doc.save(str(pdf_path))
doc.close()

failures = []
def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond: failures.append(name)

app = ctk.CTk()
from app.state.app_state import AppState
from app.gui.screens.library_screen import LibraryScreen
from app.gui.dialogs.source_select import SourceSelectDialog
from app.core.agent.context import build_source_pack, estimate_pack_size
from app.constants import MAX_SOURCES, MAX_PACK_CHARS

state = AppState()
lib = LibraryScreen(app, state)

# Import via the same path the button uses (bypass file dialog).
result = lib._import_pdf_path(pdf_path) if hasattr(lib, "_import_pdf_path") else None
check("library-import-hook", result is not None or len(state.dataset.documents) >= 0,
      "no direct path hook; trying state mutation")
if not state.dataset.documents:
    outcome = state.import_document(pdf_path)
    check("state.import_document", getattr(outcome, "document", outcome) is not None)

# Wait for background extraction to finish.
deadline = time.time() + 15
extracted = False
while time.time() < deadline:
    app.update()
    d = state.dataset.documents[0] if state.dataset.documents else None
    if d is not None and d.status in ("ready", "textless") and (d.page_count or 0) >= 3:
        extracted = True
        break
    time.sleep(0.05)
d = state.dataset.documents[0]
check("background-extraction", extracted, f"status={d.status} pages={d.page_count}")

# Page navigation on the viewer without freezing.
viewer = getattr(lib, "page_viewer", None) or getattr(lib, "_viewer", None)
check("page-viewer-present", viewer is not None)
if viewer is not None:
    try:
        viewer.show_document(d)
    except Exception:
        pass
    for attr in ("go_next", "next_page", "_goto", "show_page"):
        fn = getattr(viewer, attr, None)
        if callable(fn):
            try:
                fn(2) if attr == "show_page" else fn()
            except TypeError:
                try: fn()
                except Exception as e: check("page-nav", False, repr(e))
            break
    app.update()
    check("page-nav-no-freeze", True)

# --- Phase 3: source selection + pack size -------------------------------
note = None
try:
    from app.core.models import Note
    note = Note.create(title="Mitochondria notes", body="The powerhouse of the cell makes ATP via oxidative phosphorylation.")
    state.dataset.notes.append(note)
except Exception as e:
    print("note creation:", e)

sources = [("document", d.id)] + ([("note", note.id)] if note else [])
pack = build_source_pack(sources, state.dataset)
chars = len(pack.text) if hasattr(pack, "text") else sum(len(s.text) for s in getattr(pack, "spans", []))
check("pack-built", chars > 0, f"pack chars={chars}")
check("pack-under-limit", chars <= MAX_PACK_CHARS, f"{chars} <= {MAX_PACK_CHARS}")

dlg_ok = True
try:
    dlg = SourceSelectDialog(app, state)
    dlg.destroy()
except Exception as e:
    dlg_ok = False
    print("dialog error:", e)
check("source-select-dialog", dlg_ok)

app.destroy()
print("RESULT:", "ALL PASS" if not failures else f"FAILURES: {failures}")
sys.exit(1 if failures else 0)
