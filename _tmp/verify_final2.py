import customtkinter as ctk
from app.state.app_state import AppState
from app.core.models import Note
from datetime import date
from app.gui.screens.home_screen import HomeScreen
from app.gui.screens.calendar_screen import CalendarScreen
from app.gui.screens.notes_screen import NotesScreen
from app.gui.screens.stats_screen import StatsScreen
from app.gui.screens.settings_screen import SettingsScreen

ctk.set_appearance_mode("dark")
root = ctk.CTk(); root.geometry("1000x760")
state = AppState()
state.dataset.notes.append(Note(id="n1", title="Test note", body="hello world", day=None))
state.rebuild_index()

def build(cls, *a):
    s = cls(root, state, *a)
    s.grid(row=0, column=0, sticky="nsew")
    root.update_idletasks(); root.update()
    return s

def rel(s, w): return w.winfo_rootx() - s.winfo_rootx()

print("== HOME =="); h = build(HomeScreen)
kids = list(h.content.winfo_children())
card_ = kids[0]; inner = card_.winfo_children()[0]
print(" title:", rel(h, h.title_label), "| card border:", rel(h, card_),
      "| card text row:", rel(h, inner), "| quicklog label:",
      rel(h, next(w for w in kids if isinstance(w, ctk.CTkLabel))),
      "| preset btn:", rel(h, [c for c in kids if isinstance(c, ctk.CTkButton)][0]))

print("== CALENDAR =="); c = build(CalendarScreen)
kids = list(c.content.winfo_children())
print(" title:", rel(c, c.title_label), "| header prev btn:", rel(c, kids[0].winfo_children()[0]),
      "| grid card:", rel(c, c.grid_frame))

print("== NOTES =="); n = build(NotesScreen)
n.refresh(); root.update_idletasks(); root.update()
kids = list(n.content.winfo_children())
new_btn = [x for x in kids[0].winfo_children() if isinstance(x, ctk.CTkButton)][0]
pass
# find row frames inside the scrollable inner frame
inner_frame = None
for ch in n.list_frame.__dict__.values():
    pass
found = []
def walk(w):
    for ch in w.winfo_children():
        if isinstance(ch, ctk.CTkFrame) and ch is not n.detail_frame:
            found.append(ch); walk(ch)
        else:
            walk(ch)
walk(n.list_frame)
rowtexts = []
for f in found:
    for lbl in f.winfo_children():
        if isinstance(lbl, ctk.CTkLabel):
            t = lbl.cget("text")
            if t == "Test note":
                rowtexts.append(rel(n, lbl))
print(" title:", rel(n, n.title_label), "| New note btn:", rel(n, new_btn),
      "| list frame:", rel(n, n.list_frame), "| 'Test note' row text x:", set(rowtexts),
      "| detail card:", rel(n, n.detail_frame), "| detail title text:", rel(n, n.detail_title)+24)

print("== STATS (unchanged) =="); st = build(StatsScreen)
print(" title:", rel(st, st.title_label), "| scroll:", rel(st, st.scroll))

print("== SETTINGS =="); se = build(SettingsScreen, lambda t: None)
sec = list(se.content.winfo_children())[0]
head = sec.winfo_children()[0]; icon = head.winfo_children()[0]
print(" title:", rel(se, se.title_label), "| section card:", rel(se, sec),
      "| card icon/text:", rel(se, icon))
root.destroy()
print("ALL OK")
