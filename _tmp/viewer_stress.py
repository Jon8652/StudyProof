"""Stress the exact user-repro paths: page nav, doc switch, zoom cycling.

Runs under Xvfb with a real 4-page PDF imported through LibraryScreen, then
pumps the Tk event loop so background renders land in between clicks — the
sequence that used to raise 'TclError: image "pyimageN" doesn't exist' and
leave the viewer stuck on "Rendering page 1…".
"""
import os, sys, tempfile, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("STUDYPROOF_DATA_DIR", tempfile.mkdtemp())

import pymupdf

pdf_dir = Path(tempfile.mkdtemp())


def make_pdf(name, pages):
    src = pdf_dir / name
    doc = pymupdf.open()
    for i in range(pages):
        p = doc.new_page()
        p.insert_text((72, 72 + 40 * i), f"{name} page {i+1}: study content.")
    doc.save(src)
    doc.close()
    return src


import customtkinter as ctk
from app.state.app_state import AppState
from app.gui.screens.library_screen import LibraryScreen

root = ctk.CTk()
root.geometry("1100x700")
state = AppState()
state.load_startup()
screen = LibraryScreen(root, state)
screen.pack(fill="both", expand=True)


# Non-blocking message dialogs would otherwise hold a grab forever in
# headless runs; stub them so _import_path failures surface as asserts.
import app.gui.screens.library_screen as libscreen
libscreen.show_message = lambda *a, **k: print("MESSAGE:", a[1:])
libscreen.show_error = lambda *a, **k: print("ERROR:", a[1:])


def pump(n=30):
    for _ in range(n):
        root.update_idletasks()
        root.update()
        time.sleep(0.02)


def check_no_errors():
    # Any exception inside a Tk callback/thread would have printed a
    # traceback; here we assert widget state is sane instead.
    v = screen.viewer
    assert v.image_label.winfo_exists()


# Import two documents with distinct sizes so the (name, size) duplicate
# guard never refuses them (second -> exercises document switching).
def import_doc(name, pages):
    for attempt in range(pages + 2):
        src = make_pdf(name, pages)
        src.write_bytes(src.read_bytes() + b"% " + b"x" * (attempt * 7) + b"\n")
        if screen._import_path(src):
            return True
        pump(5)
    return False


assert import_doc("alpha.pdf", 4)
pump(80)  # let extraction finish
assert import_doc("beta.pdf", 3)
pump(80)

doc_a_id = state.get_documents()[0].id if len(state.get_documents()) else None
ids = [d.id for d in state.get_documents()]
print("docs:", ids)

# 1) Page navigation back and forth on current doc
screen.select_document(ids[0])
pump()
for _ in range(6):
    screen.viewer._go_next()
    pump(6)
    screen.viewer._go_prev()
    pump(6)
print("nav ok, page label:", screen.viewer.page_label.cget("text"))

# 2) Zoom cycling (retires cache each click - old crash source)
for _ in range(9):
    screen.viewer._cycle_zoom()
    pump(10)
print("zoom ok, button:", screen.viewer.zoom_button.cget("text"))

# 3) Document switching mid-render (stale-result guard)
for _ in range(4):
    screen.select_document(ids[1])
    screen.viewer._go_next()          # fire a render...
    screen.select_document(ids[0])    # ...then swap docs immediately
    pump(15)
print("switch ok")

# 4) Text/image toggle spam
for _ in range(6):
    screen.viewer._toggle_text()
    pump(5)

# 5) GC pressure: force collection while images are swapped/evicted
import gc
for _ in range(3):
    screen.viewer._cycle_zoom()
    screen.viewer._go_next()
    pump(8)
    gc.collect()

# Final state must show an actual rendered image, not a stuck placeholder
label_text = screen.viewer.image_label.cget("text")
has_image = screen.viewer.image_label.has_image
print("final: has_image =", has_image, "| label text =", repr(label_text[:40]))
assert has_image, f"viewer ended without an image (text={label_text!r})"
assert "Rendering" not in label_text

check_no_errors()
root.destroy()
print("VIEWER STRESS PASS")
