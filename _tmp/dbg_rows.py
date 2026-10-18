import customtkinter as ctk
from app.state.app_state import AppState
from app.core.models import Note
from app.gui.screens.notes_screen import NotesScreen
ctk.set_appearance_mode("dark")
root = ctk.CTk(); root.geometry("1000x760")
state = AppState()
state.dataset.notes.append(Note(id="n1", title="Test note", body="hello world", day=None))
state.rebuild_index()
n = NotesScreen(root, state)
n.grid(row=0, column=0, sticky="nsew")
n.refresh(); root.update_idletasks(); root.update()
base = n.winfo_rootx()
def walk(w, d=0):
    for ch in w.winfo_children():
        t = ch.cget("text") if isinstance(ch, (ctk.CTkLabel, ctk.CTkButton)) else ""
        print(" "*d, type(ch).__name__, "x=", ch.winfo_rootx()-base, repr(t)[:30])
        walk(ch, d+2)
walk(n.list_frame)
