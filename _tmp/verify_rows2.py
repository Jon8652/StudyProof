import customtkinter as ctk
from datetime import date
from app.state.app_state import AppState
from app.core.models import Note
from app.gui.screens.notes_screen import NotesScreen

ctk.set_appearance_mode("dark")
root = ctk.CTk(); root.geometry("1000x760")
state = AppState()
# add a note directly to the dataset
state.dataset.notes.append(Note(id="n1", title="Test note", body="hello", day=None))
state.rebuild_index(); state.dirty = True
s = NotesScreen(root, state)
s.grid(row=0, column=0, sticky="nsew")
root.update_idletasks(); root.update()
base = s.winfo_rootx()
def rel(w): return w.winfo_rootx() - base

print("title label edge :", rel(s.title_label))
print("New note btn     :", rel([c for c in s.content.winfo_children()[0].winfo_children() if isinstance(c, ctk.CTkButton)][0]))
print("list frame       :", rel(s.list_frame))
canvas_children = s.list_frame._parent_frame.winfo_children()
for c in canvas_children:
    print("  child:", type(c).__name__, "rel_x=", rel(c))
    for cc in c.winfo_children():
        print("    sub:", type(cc).__name__, "rel_x=", rel(cc),
              [(type(x).__name__, rel(x)) for x in cc.winfo_children()][:3])
