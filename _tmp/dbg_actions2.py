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
        n.id = f"n{len(self._notes)}"; n.display_title = lambda: title
        n.body = body; n.day = day
        n.updated_at_local = dt.datetime(2026,10,13,9,30)
        self._notes.append(n); return n

ctk.set_appearance_mode("dark")
root = ctk.CTk(); root.geometry("1000x700")
state = FakeState()
plain = state.create_note("Plain note", "hello world")
cal = state.create_note("Calendar note", "created from calendar", day=dt.date(2026,10,12))
screen = NotesScreen(root, state); screen.pack(fill="both", expand=True)
root.update()

for nid, label in [(cal.id,"CAL"), (plain.id,"PLAIN"), (cal.id,"CAL again"), (plain.id,"PLAIN again")]:
    screen._select(nid); root.update_idletasks(); root.update()
    a = screen.edit_button.master
    order = [c.cget("text")[:9] for c in a.pack_slaves()]
    print(label, "| actions reqw:", a.winfo_reqwidth(), "| pack order:", order)
    for c in a.pack_slaves():
        info = c.pack_info()
        print("   ", repr(c.cget("text")[:12]), "side=", info["side"], "padx=", info["padx"], "reqw=", c.winfo_reqwidth(), "x=", c.winfo_x())
    print("    detail actual w:", screen.detail_frame.winfo_width(), "list actual w:", screen.list_frame.winfo_width())
