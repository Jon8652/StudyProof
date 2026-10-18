"""Measure list/detail split for calendar vs plain note selection."""
import datetime as dt
import customtkinter as ctk
from app.gui.screens.notes_screen import NotesScreen

class FakeDataset:
    notes = []
    def __init__(self, owner): self._owner = owner
    def note_by_id(self, nid):
        for n in self._owner._notes:
            if n.id == nid: return n
        return None

class FakeState:
    def __init__(self):
        self.dataset = FakeDataset(self)
        self._notes = []
    def get_all_notes(self): return self._notes
    def create_note(self, title, body="", day=None):
        n = type("N", (), {})()
        n.id = f"n{len(self._notes)}"
        n.display_title = lambda: title
        n.body = body
        n.day = day
        n.updated_at_local = dt.datetime(2026, 10, 13, 9, 30)
        self._notes.append(n)
        return n

ctk.set_appearance_mode("dark")
root = ctk.CTk()
root.geometry("1000x700")
state = FakeState()
plain = state.create_note("Plain note", "hello world")
cal   = state.create_note("Calendar note", "created from calendar", day=dt.date(2026,10,12))

screen = NotesScreen(root, state)
screen.pack(fill="both", expand=True)
root.update_idletasks(); root.update()

def snap(tag):
    lf, df = screen.list_frame, screen.detail_frame
    print(f"{tag}: list x={lf.winfo_x()} w={lf.winfo_width()} | detail x={df.winfo_x()} w={df.winfo_width()} | actions reqw={screen.edit_button.master.winfo_reqwidth()}")

screen._select(cal.id);   root.update_idletasks(); root.update(); snap("CALENDAR (Open day shown)")
screen._select(plain.id); root.update_idletasks(); root.update(); snap("PLAIN   (Open day hidden)")
screen._select(cal.id);   root.update_idletasks(); root.update(); snap("CALENDAR again")
