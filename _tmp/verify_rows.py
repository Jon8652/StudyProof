from __future__ import annotations
import datetime as dt
import customtkinter as ctk
from app.state.app_state import AppState
from app.gui.screens.notes_screen import NotesScreen

def inner_text_x(widget):
    w = getattr(widget, "_label", widget)
    w.update_idletasks()
    return w.winfo_rootx()

def main():
    ctk.set_appearance_mode("dark"); ctk.set_default_color_theme("blue")
    app = ctk.CTk(); app.geometry("1200x800")
    state = AppState()
    # regular note (created from the notes screen, no day)
    state.create_note("Regular note", "Body of the regular note.")
    # calendar-created notes: attached to a day -> have a date tag line
    state.create_note("Calendar note", "Short body.", day=dt.date(2026, 10, 13))
    state.create_note("X", "y", day=dt.date(2026, 9, 1))  # very short title/date

    notes = NotesScreen(app, state)
    notes.grid(row=0, column=0, sticky="nsew")
    app.grid_rowconfigure(0, weight=1); app.grid_columnconfigure(0, weight=1)
    app.update_idletasks(); app.update()

    lf = notes.list_frame
    header = [c for c in lf._parent_frame.winfo_children() if isinstance(c, ctk.CTkLabel)][0]
    print("header 'All notes' text x:", inner_text_x(header))
    rows = [c for c in lf.winfo_children() if isinstance(c, ctk.CTkFrame)]
    xs = set()
    for i, r in enumerate(rows):
        for lbl in r.winfo_children():
            x = inner_text_x(lbl)
            xs.add(x)
            print(f"row {i} [{lbl._label.cget('text')[:24]!r}] text x = {x}")
    print("distinct text left edges:", sorted(xs))
    # selection still works?
    first = rows[0].winfo_children()[0]
    first.event_generate("<Button-1>")
    app.update()
    print("selected id set:", notes._selected_note_id is not None)
    print("detail title:", notes.detail_title.cget("text"))

main()
