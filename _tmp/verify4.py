import sys, tempfile
from pathlib import Path
sys.path.insert(0, "/workspace")
from app.utils import paths
paths.set_override_data_dir(Path(tempfile.mkdtemp()))
import customtkinter as ctk, tkinter
from datetime import date
from app.state.app_state import AppState

root = ctk.CTk()
root.geometry("1280x860+0+0")
state = AppState()
n1 = state.create_note("First note", "Body of first note", day=date.today())
state.create_note("Second", "Another body")

def dump(widget, tag):
    out = []
    def walk(w):
        if isinstance(w, (tkinter.Label, tkinter.Text)) and w.winfo_ismapped():
            pad = int(str(w.cget("padx")) or 0)
            txt = w.cget("text") if isinstance(w, tkinter.Label) else "<TEXT>"
            out.append((w.winfo_rootx() + pad, txt[:34].replace("\n", "|")))
        for c in w.winfo_children():
            walk(c)
    walk(widget)
    for x, t in sorted(out):
        print(tag, "x=%.1f" % x, repr(t))
    return out

from app.gui.screens.notes_screen import NotesScreen
ns = NotesScreen(root, state)
ns.grid(row=0, column=0, sticky="nsew")
root.grid_rowconfigure(0, weight=1)
root.grid_columnconfigure(0, weight=1)
for _ in range(5):
    root.update(); root.update_idletasks()
print("== NOTES (day-attached note selected) ==")
# select the note that has a day so Open day is visible
notes = state.get_all_notes()
target = [n for n in notes if n.day is not None][0]
ns._select(target.id)
for _ in range(5):
    root.update(); root.update_idletasks()
dump(ns.list_frame._label, "HEADER:")
dump(ns.list_frame, "LISTROW:")
dump(ns.detail_frame, "DETAIL:")
root.destroy()
