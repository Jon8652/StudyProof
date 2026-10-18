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
root = ctk.CTk(); root.geometry("1000x700")
state = FakeState()
plain = state.create_note("Plain note", "hello world")
cal   = state.create_note("Calendar note with a very long title that would previously wrap at 460px and shove the layout around", "created from calendar", day=dt.date(2026,10,12))
empty = state.create_note("Empty body", "", day=dt.date(2026,10,11))

screen = NotesScreen(root, state); screen.pack(fill="both", expand=True)
root.update_idletasks(); root.update()

def snap(tag):
    lf, df = screen.list_frame, screen.detail_frame
    ab, db, ob = screen.edit_button, screen.delete_button, screen.open_day_button
    gaps = [db.winfo_x() - (ab.winfo_x()+ab.winfo_width())]
    if ob.winfo_manager():
        gaps.append(ob.winfo_x() - (db.winfo_x()+db.winfo_width()))
    print(f"{tag}: list w={lf.winfo_width()} detail w={df.winfo_width()} btn-gaps={gaps} title x={screen.detail_title.winfo_x()} w={screen.detail_title.winfo_width()}")

for nid, tag in [(cal.id,"CAL-long"), (plain.id,"PLAIN"), (empty.id,"EMPTY-cal"), (None,"NONE")]:
    if nid is None:
        screen._selected_note_id = None; screen._render_list(); screen._render_detail()
    else:
        screen._select(nid)
    root.update_idletasks(); root.update(); snap(tag)

# resize window narrower to make sure split stays proportional & stable
root.geometry("700x600"); root.update_idletasks(); root.update()
screen._select(cal.id); root.update_idletasks(); root.update(); snap("CAL @700w")
screen._select(plain.id); root.update_idletasks(); root.update(); snap("PLAIN @700w")
print("OK")
