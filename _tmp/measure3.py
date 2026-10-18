import sys, tempfile
from pathlib import Path
sys.path.insert(0, "/workspace")
from app.utils import paths
paths.set_override_data_dir(Path(tempfile.mkdtemp()))
import customtkinter as ctk, tkinter
from datetime import date
from app.state.app_state import AppState

root = ctk.CTk()
root.geometry("1200x800")
state = AppState()
state.create_note("First note", "Body of first note", day=date.today())
state.create_note("Second", "Another body")

def dump(widget, tag):
    def walk(w):
        if isinstance(w, (tkinter.Label, tkinter.Text)):
            w.update_idletasks()
            pad = int(str(w.cget("padx")) or 0)
            txt = w.cget("text") if isinstance(w, tkinter.Label) else "<TEXT>"
            print(tag, "x=%.1f" % (w.winfo_rootx() + pad), "mapped=%d" % w.winfo_ismapped(), repr(txt[:36]))
        for c in w.winfo_children():
            walk(c)
    walk(widget)

from app.gui.screens.notes_screen import NotesScreen
ns = NotesScreen(root, state)
root.update(); root.update_idletasks(); root.update()
dump(ns.list_frame._label, "HEADER:")
dump(ns.list_frame, "LISTROW:")
dump(ns.detail_frame, "DETAIL:")

from app.gui.screens.settings_screen import SettingsScreen
ss = SettingsScreen(root, state)
root.update(); root.update_idletasks(); root.update()
dump(ss, "SETTINGS:")
root.destroy()
