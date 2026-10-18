import customtkinter as ctk
from app.state.app_state import AppState
from app.core.models import Note
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

fails = []
def chk(name, val, expect):
    ok = abs(val - expect) <= 1
    print(f"  {name:<30} x={val:>4} (expect {expect}) {'OK' if ok else 'FAIL'}")
    if not ok: fails.append(name)

print("== HOME =="); h = build(HomeScreen)
kids = list(h.content.winfo_children())
card_ = kids[0]; inner = card_.winfo_children()[0]
chk("title", rel(h, h.title_label), 24)
chk("today card border", rel(h, card_), 24)
# CTkLabel text inset for size-16 bold font ~ measured offset
lbl = inner.winfo_children()[0]
chk("card inner label frame", rel(h, inner), 48)
chk("Quick log label", rel(h, next(w for w in kids if isinstance(w, ctk.CTkLabel))), 24)
btns = [w for w in kids if isinstance(w, ctk.CTkButton)]
chk("custom-session button", rel(h, btns[0]), 24)

print("== CALENDAR =="); c = build(CalendarScreen)
kids = list(c.content.winfo_children())
chk("title", rel(c, c.title_label), 24)
chk("prev-month button", rel(c, kids[0].winfo_children()[0]), 24)
chk("grid card", rel(c, c.grid_frame), 24)

print("== NOTES =="); n = build(NotesScreen)
n.refresh(); root.update_idletasks(); root.update()
kids = list(n.content.winfo_children())
new_btn = [x for x in kids[0].winfo_children() if isinstance(x, ctk.CTkButton)][0]
chk("title", rel(n, n.title_label), 24)
chk("New note button", rel(n, new_btn), 24)
found = []
def walk(w):
    for ch in w.winfo_children():
        found.append(ch); walk(ch)
walk(n.list_frame)
rowframes = [f for f in found if isinstance(f, ctk.CTkFrame) and any(
    isinstance(l, ctk.CTkLabel) and l.cget("text")=="Test note" for l in f.winfo_children())]
chk("note row card left edge", rel(n, rowframes[0]), 45)

print("== STATS (unchanged) =="); st = build(StatsScreen)
chk("title", rel(st, st.title_label), 0)
chk("scroll frame", rel(st, st.scroll), 6)

print("== SETTINGS =="); se = build(SettingsScreen, lambda t: None)
sec = list(se.content.winfo_children())[0]
icon = sec.winfo_children()[0].winfo_children()[0]
chk("title", rel(se, se.title_label), 24)
chk("section card", rel(se, sec), 24)
chk("card icon/text", rel(se, icon), 48)

root.destroy()
print("RESULT:", "ALL OK" if not fails else f"FAILURES: {fails}")
