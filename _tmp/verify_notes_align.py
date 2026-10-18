import customtkinter as ctk
from app.state.app_state import AppState
from app.gui.screens.notes_screen import NotesScreen

ctk.set_appearance_mode("dark")
root = ctk.CTk(); root.geometry("1000x760")
s = NotesScreen(root, AppState())
s.grid(row=0, column=0, sticky="nsew")
root.update_idletasks(); root.update()

base = s.winfo_rootx()
def rel(w): return w.winfo_rootx() - base

print("title label edge      :", rel(s.title_label))
new_btn = [c for c in s.content.winfo_children()[0].winfo_children() if isinstance(c, ctk.CTkButton)][0]
print("New note btn edge     :", rel(new_btn))
print("list frame edge       :", rel(s.list_frame))
# list rows: scrollable inner canvas -> find first row frame after refresh
s.refresh(); root.update_idletasks(); root.update()
rows = [c for c in s.list_frame._parent_frame.winfo_children() if isinstance(c, ctk.CTkFrame)]
print("note rows found       :", len(rows))
for r in rows[:2]:
    # r is the pack frame; its child is the row button/frame
    print("  row frame edge        :", rel(r), " children:", [(type(c).__name__, rel(c)) for c in r.winfo_children()])
print("detail card edge      :", rel(s.detail_frame))
print("detail title text edge:", rel(s.detail_title) + 24)  # grid padx inside card
