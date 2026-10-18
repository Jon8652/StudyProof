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
    def create_note(self, title, body="", day=None, when=dt.datetime(2026,10,13,9,30)):
        n = type("N", (), {})()
        n.id = f"n{len(self._notes)}"
        n.display_title = lambda: title
        n.body = body; n.day = day
        n.updated_at_local = when
        self._notes.append(n); return n

ctk.set_appearance_mode("dark")
root = ctk.CTk(); root.geometry("1000x700")
state = FakeState()
plain = state.create_note("Plain note", "hello world")
cal   = state.create_note("Calendar note with a very long title that would previously wrap at 460px and shove the layout around", "created from calendar", day=dt.date(2026,10,12), when=dt.datetime(2026,10,13,9,30))
cal2  = state.create_note("Short", "x", day=dt.date(2026,10,1), when=dt.datetime(2025,1,1,0,0))  # different timestamp width
empty = state.create_note("Empty body", "", day=dt.date(2026,10,11))

screen = NotesScreen(root, state); screen.pack(fill="both", expand=True)

def settle():
    for _ in range(30):
        root.update_idletasks(); root.update()

def snap(tag):
    lf, df = screen.list_frame, screen.detail_frame
    ab, db, ob = screen.edit_button, screen.delete_button, screen.open_day_button
    gaps = [db.winfo_x() - (ab.winfo_x()+ab.winfo_width())]
    if ob.winfo_manager():
        gaps.append(ob.winfo_x() - (db.winfo_x()+db.winfo_width()))
    print(f"{tag}: list x={lf.winfo_x()} w={lf.winfo_width()} | detail x={df.winfo_x()} w={df.winfo_width()} | btn-gaps={gaps} | meta='{screen.detail_meta.cget('text')}'")

for nid, tag in [(None,"NONE"), (cal.id,"CAL-long"), (plain.id,"PLAIN"), (cal2.id,"CAL-short-diffstamp"), (empty.id,"EMPTY-cal"), (cal.id,"CAL again"), (plain.id,"PLAIN again")]:
    if nid is None:
        screen._selected_note_id = None; screen._render_list(); screen._render_detail()
    else:
        screen._select(nid)
    settle(); snap(tag)

root.geometry("700x600"); settle()
screen._select(cal.id); settle(); snap("CAL @700w")
screen._select(plain.id); settle(); snap("PLAIN @700w")
print("ALL OK")
