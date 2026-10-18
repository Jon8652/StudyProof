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
app.geometry("1280x800"); pump(15)
app.show_screen("home"); pump(6)
ws=[]; walk(app.screens["home"].content, ws)
btns=[w for w in ws if isinstance(w, ctk.CTkButton)]
presets=[b for b in btns if b.cget("text") in ("30 minutes","1 hour","2 hours")]
frame = presets[0].master
print("frame class:", type(frame).__name__, "width:", frame.winfo_width(),
      "x:", frame.winfo_rootx(), "reqwidth:", frame.winfo_reqwidth())
print("children of content:", [(type(w).__name__, getattr(w,'cget',lambda k:'')('fg_color') if hasattr(w,'cget') else '') for w in ws if w.master is app.screens['home'].content or (hasattr(w,'_parent_frame') and w.master is app.screens['home'].content)])
for b in presets:
    print(b.cget("text"), "geom x/abs:", b.winfo_x(), b.winfo_rootx(), "w:", b.winfo_width(),
          "reqw:", b.winfo_reqwidth(), "grid info:", b.grid_info())
print("colconf:", [frame.grid_columnconfigure(i) for i in range(3)][0])
