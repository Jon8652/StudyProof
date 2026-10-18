import customtkinter as ctk
from app.gui.main_window import MainWindow
from app.state.app_state import AppState

ctk.set_appearance_mode("dark")
app = MainWindow(app_state=AppState())
app.run_startup_load()

def pump(n=8):
    for _ in range(n):
        app.update_idletasks(); app.update()

def walk(w, out):
    for c in w.winfo_children():
        out.append(c)
        walk(c, out)

# --- HOME: preset buttons vs custom button ---------------------------------
app.show_screen("home"); pump()
home = app.screens["home"]
ws = []; walk(home.content, ws)
btns = [w for w in ws if isinstance(w, ctk.CTkButton)]
preset_btns = [b for b in btns if "30" in b.cget("text") or "1 hour" in b.cget("text") or "2 hour" in b.cget("text")]
print("preset texts:", [b.cget("text") for b in btns])
cst = [b for b in btns if "custom" in b.cget("text").lower()][0]
pump(4)
p0, p1, p2 = preset_btns[:3]
print("PRESET widths:", p0.winfo_width(), p1.winfo_width(), p2.winfo_width())
r3 = p2.winfo_x() + p2.winfo_width()
rc = cst.winfo_x() + cst.winfo_width()
# compare in same parent coords via rootx
r3 = p2.winfo_rootx() + p2.winfo_width()
rc = cst.winfo_rootx() + cst.winfo_width()
print("Preset3 right:", r3, "Custom right:", rc)
assert p0.winfo_width() == p1.winfo_width() == p2.winfo_width(), "presets unequal"
assert abs(r3 - rc) <= 1, "right edges misaligned"
# left alignment on home
labels = [w for w in ws if isinstance(w, ctk.CTkLabel) and w.cget("text")=="Quick log"]
print("title x:", home.title_label.winfo_rootx() if hasattr(home,'title_label') else [w.winfo_rootx() for w in ws if isinstance(w, ctk.CTkLabel)][:1])
print("quicklog x:", labels[0].winfo_rootx(), "preset1 x:", p0.winfo_rootx(), "custom x:", cst.winfo_rootx())

# --- NOTES panes ------------------------------------------------------------
app.show_screen("notes"); pump(12)
notes = app.screens["notes"]
lw = notes.list_frame.winfo_width(); dw = notes.detail_frame.winfo_width()
print("Notes list:", lw, "detail:", dw, "ratio:", round(lw/dw,3))
assert lw < dw and lw > 0.45*dw

# --- STATS alignment --------------------------------------------------------
app.show_screen("stats"); pump(12)
stats = app.screens["stats"]
ws2 = []; walk(stats, ws2)
title = [w for w in ws2 if isinstance(w, ctk.CTkLabel) and w.cget("text")=="Statistics"][0]
print("Stats title x:", title.winfo_rootx(), "| Home title x:", )
# find first card inside scroll
cards = [w for w in ws2 if type(w).__name__=="SummaryCard"]
if cards: print("First stat card x:", cards[0].winfo_rootx())
# compare with another screen's title x
home_title = [w for w in ws if isinstance(w, ctk.CTkLabel) and w.cget("text")=="Log Study"]
if home_title: print("Home title x:", home_title[0].winfo_rootx())
print("OK")
