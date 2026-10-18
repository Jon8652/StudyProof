import customtkinter as ctk
from app.gui.main_window import MainWindow
from app.state.app_state import AppState

ctk.set_appearance_mode("dark")
app = MainWindow(app_state=AppState())
app.run_startup_load()

def pump(n=10):
    for _ in range(n):
        app.update_idletasks(); app.update()

def walk(w, out):
    for c in w.winfo_children():
        out.append(c); walk(c, out)

# preload all screens then resize to settle grid geometry
for k in ("home","calendar","notes","stats","settings"):
    app.show_screen(k); pump(3)
app.geometry("1280x800")
pump(15)
app.show_screen("home"); pump(6)

home = app.screens["home"]
ws=[]; walk(home.content, ws)
btns=[w for w in ws if isinstance(w, ctk.CTkButton)]
presets=[b for b in btns if b.cget("text") in ("30 minutes","1 hour","2 hours")]
cst=[b for b in btns if "custom" in b.cget("text").lower()][0]
widths=[b.winfo_width() for b in presets]
edges=[(b.winfo_rootx(), b.winfo_rootx()+b.winfo_width()) for b in presets]
print("preset widths:", widths)
print("preset edges:", edges)
print("custom edge:", cst.winfo_rootx(), cst.winfo_rootx()+cst.winfo_width())
assert len(set(widths))==1, "presets unequal"
assert abs(edges[-1][1]-(cst.winfo_rootx()+cst.winfo_width()))<=1, "right misaligned"
gaps=[edges[1][0]-edges[0][1], edges[2][0]-edges[1][1]]
print("gaps:", gaps)

labels=[w for w in ws if isinstance(w, ctk.CTkLabel) and w.cget("text")=="Quick log"]
today_card=[w for w in ws if type(w).__name__=="CTkFrame" and w.winfo_parent()==home.content._canvas._parent_frame.__class__.__name__ ]
print("Quick log x:", labels[0].winfo_rootx(), "preset1 x:", presets[0].winfo_rootx())

# notes panes
app.show_screen("notes"); pump(8)
n=app.screens["notes"]
lw=n.list_frame.winfo_width(); dw=n.detail_frame.winfo_width()
print("notes list/detail:", lw, dw, round(lw/dw,3))
assert lw<dw and lw>0.45*dw

# stats alignment vs home
app.show_screen("stats"); pump(8)
s=app.screens["stats"]
ws2=[]; walk(s, ws2)
stitle=[w for w in ws2 if isinstance(w, ctk.CTkLabel) and w.cget("text")=="Statistics"][0]
htitle=[w for w in walk(home,[]) or [] if False]
hw=[]; walk(home,hw)
h_title=[w for w in hw if isinstance(w, ctk.CTkLabel) and w.cget("text")=="Log Study"][0]
print("Stats title x:", stitle.winfo_rootx(), "Home title x:", h_title.winfo_rootx())
cards=[w for w in ws2 if type(w).__name__=="SummaryCard"]
print("first stat card x:", cards[0].winfo_rootx())
lastcard=cards[-1]
print("scroll right:", )
sf=[w for w in ws2 if type(w).__name__=="CTkScrollableFrame"][0]
print("stat row right edge (card4):", cards[3].winfo_rootx()+cards[3].winfo_width())
assert stitle.winfo_rootx()==h_title.winfo_rootx(), "title x mismatch"
print("ALL OK")
