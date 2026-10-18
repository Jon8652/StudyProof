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
state.create_note("First note", "Body of first note", day=date.today())
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
    for x, t in out:
        print(tag, "x=%.1f" % x, repr(t))
    return out

from app.gui.screens.notes_screen import NotesScreen
ns = NotesScreen(root, state)
for _ in range(3):
    root.update(); root.update_idletasks()
print("== NOTES ==")
dump(ns.list_frame._label, "HEADER:")
dump(ns.list_frame, "LISTROW:")
dump(ns.detail_frame, "DETAIL:")

from app.gui.screens.settings_screen import SettingsScreen
ss = SettingsScreen(root, state)
for _ in range(3):
    root.update(); root.update_idletasks()
print("== SETTINGS ==")
dump(ss, "SETTINGS:")
root.destroy()
