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

for k in ("home","calendar","notes","stats","settings"):
    app.show_screen(k); pump(3)

def check(width, height):
    app.geometry(f"{width}x{height}")
    pump(25)
    app.show_screen("home"); pump(6)
    home = app.screens["home"]
    ws=[]; walk(home.content, ws)
    btns=[w for w in ws if isinstance(w, ctk.CTkButton)]
    presets=[b for b in btns if b.cget("text") in ("30 minutes","1 hour","2 hours")]
    cst=[b for b in btns if "custom" in b.cget("text").lower()][0]
    widths=[b.winfo_width() for b in presets]
    edges=[(b.winfo_rootx(), b.winfo_rootx()+b.winfo_width()) for b in presets]
    ok_eq = len(set(widths))==1
    ok_right = edges[-1][0] < cst.winfo_rootx() and 0 <= (cst.winfo_rootx()+cst.winfo_width())-edges[-1][1] <= 2
    print(f"size {width}x{height}: widths={widths} eq={ok_eq} "
          f"p3right={edges[-1][1]} customright={cst.winfo_rootx()+cst.winfo_width()} aligned={ok_right}")
    assert ok_eq and ok_right

check(1280, 800)
check(1000, 700)
check(1400, 900)

# notes panes at final size
app.show_screen("notes"); pump(8)
n=app.screens["notes"]
lw=n.list_frame.winfo_width(); dw=n.detail_frame.winfo_width()
print("notes list/detail:", lw, dw, "ratio", round(lw/dw,3))
assert lw < dw and lw > 0.45*dw

# stats alignment
app.show_screen("stats"); pump(8)
s=app.screens["stats"]
ws2=[]; walk(s, ws2)
stitle=[w for w in ws2 if isinstance(w, ctk.CTkLabel) and w.cget("text")=="Statistics"][0]
hw=[]; walk(app.screens["home"], hw)
htitle=[w for w in hw if isinstance(w, ctk.CTkLabel) and w.cget("text")=="Log Study"][0]
cards=[w for w in ws2 if type(w).__name__=="SummaryCard"]
print("Stats title x:", stitle.winfo_rootx(), "== Home title x:", htitle.winfo_rootx(),
      "| first card x:", cards[0].winfo_rootx())
assert stitle.winfo_rootx()==htitle.winfo_rootx()
cw=[w for w in hw if isinstance(w, ctk.CTkFrame)][0]
print("Home card x:", cw.winfo_rootx())
print("ALL OK")
