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
app.geometry("1280x800"); pump(25)
app.show_screen("home"); pump(10)
ws=[]; walk(app.screens["home"].content, ws)
btns=[w for w in ws if isinstance(w, ctk.CTkButton)]
presets=[b for b in btns if b.cget("text") in ("30 minutes","1 hour","2 hours")]
cst=[b for b in btns if "custom" in b.cget("text").lower()][0]
print("widths:", [b.winfo_width() for b in presets])
print("xs:", [b.winfo_rootx() for b in presets])
print("ys:", [b.winfo_rooty() for b in presets], "custom y:", cst.winfo_rooty())
print("right edges:", [b.winfo_rootx()+b.winfo_width() for b in presets], "custom right:", cst.winfo_rootx()+cst.winfo_width())
