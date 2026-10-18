import customtkinter as ctk
from app.state.app_state import AppState
from app.gui.screens.notes_screen import NotesScreen
ctk.set_appearance_mode("dark"); ctk.set_default_color_theme("blue")
app = ctk.CTk(); app.geometry("1200x800")
state = AppState()
state.create_note("Regular note", "Body of the regular note.")
notes = NotesScreen(app, state)
notes.grid(row=0, column=0, sticky="nsew")
app.grid_rowconfigure(0, weight=1); app.grid_columnconfigure(0, weight=1)
app.update_idletasks(); app.update()
lf = notes.list_frame
header = [c for c in lf._parent_frame.winfo_children() if isinstance(c, ctk.CTkLabel)][0]
print("OLD header inner x:", header._label.winfo_rootx(), "outer:", header.winfo_rootx())
print("OLD canvas inset  :", lf._parent_canvas.winfo_rootx())
rows = [c for c in lf.winfo_children() if isinstance(c, ctk.CTkButton)]
for r in rows:
    print("OLD row bg x:", r.winfo_rootx(), "text x:", r._text_label.winfo_rootx())
