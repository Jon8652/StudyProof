"""Verify: detail-pane meta shows only 'Updated ...', and the actions row
width (hence list/detail split) is identical for calendar vs plain notes."""
import datetime as dt
import tkinter as tk
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
    def get_all_notes(self):
        return self._notes
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
cal   = state.create_note("Calendar note", "created from calendar", day=dt.date(2026, 10, 12))

screen = NotesScreen(root, state)
screen.pack(fill="both", expand=True)
root.update_idletasks(); root.update()

def measure(note_id):
    screen._select(note_id)
    root.update_idletasks(); root.update()
    meta = screen.detail_meta.cget("text")
    ab = screen.edit_button; db = screen.delete_button; ob = screen.open_day_button
    gaps = {}
    gaps["edit->delete"] = db.winfo_x() - (ab.winfo_x() + ab.winfo_width())
    if ob.winfo_manager():
        gaps["delete->openday"] = ob.winfo_x() - (db.winfo_x() + db.winfo_width())
    actions_w = screen.edit_button.master.winfo_reqwidth()
    list_w = screen.list_frame.winfo_width()
    detail_w = screen.detail_frame.winfo_width()
    return meta, gaps, actions_w, list_w, detail_w

m1 = measure(cal.id)   # calendar note -> Open day visible
m2 = measure(plain.id) # plain note -> Open day hidden
print("CALENDAR note meta:", repr(m1[0]))
print("  gaps:", m1[1], "actions reqwidth:", m1[2], "list/detail width:", m1[3], m1[4])
print("PLAIN note meta:   ", repr(m2[0]))
print("  gaps:", m2[1], "actions reqwidth:", m2[2], "list/detail width:", m2[3], m2[4])

assert m1[0].startswith("Updated ") and "2026" in m1[0]
assert len(m1[0]) < len("Updated 2026-10-13 09:30") + 5, m1[0]
assert "October" not in m1[0], "day date should no longer appear"
assert m1[1]["edit->delete"] == 8
assert m1[1].get("delete->openday") == 8, m1[1]
assert m1[3] == m2[3] and m1[4] == m2[4], "split changed between note types!"
print("ALL OK")
root.destroy()
