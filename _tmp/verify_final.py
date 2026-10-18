import datetime as dt
import customtkinter as ctk
from app.state.app_state import AppState
from app.gui.screens.notes_screen import NotesScreen

ctk.set_appearance_mode("dark"); ctk.set_default_color_theme("blue")
app = ctk.CTk(); app.geometry("1200x800")
state = AppState()
state.create_note("Regular note", "Body of the regular note.")
state.create_note("Calendar note", "Short body.", day=dt.date(2026, 10, 13))
notes = NotesScreen(app, state)
notes.grid(row=0, column=0, sticky="nsew")
app.grid_rowconfigure(0, weight=1); app.grid_columnconfigure(0, weight=1)
app.update_idletasks(); app.update()

lf = notes.list_frame
header = [c for c in lf._parent_frame.winfo_children() if isinstance(c, ctk.CTkLabel)][0]
rows = [c for c in lf.winfo_children() if isinstance(c, ctk.CTkFrame)]
print("row bg x (all should match):", sorted({r.winfo_rootx() for r in rows}))
for i, r in enumerate(rows):
    texts = [(l._label.cget("text"), l._label.winfo_rootx()) for l in r.winfo_children()]
    print(f"row {i}:", texts)
# hover no longer exists; selection re-render keeps working
notes._select(rows[1].winfo_children()[0]) if False else None
rows[1].winfo_children()[0].event_generate("<Button-1>")
app.update(); app.update_idletasks()
rows2 = [c for c in notes.list_frame.winfo_children() if isinstance(c, ctk.CTkFrame)]
print("after click, distinct text x:", sorted({l._label.winfo_rootx() for r in rows2 for l in r.winfo_children()}))
print("detail title x:", notes.detail_title._label.winfo_rootx(), "body x:", notes.detail_body._textbox.winfo_rootx())
# empty-state label alignment
notes.search_entry.insert(0, "zzz"); notes.refresh(); app.update_idletasks(); app.update()
lbl = [c for c in notes.list_frame.winfo_children() if isinstance(c, ctk.CTkLabel)]
print("empty label x:", [l._label.winfo_rootx() for l in lbl], "header x:", header._label.winfo_rootx())
