import sys; sys.path.insert(0, "/workspace")
import customtkinter as ctk
from app.gui.widgets.page_viewer import PageViewer
from app.gui.workers import UIEventQueue

root = ctk.CTk(); root.geometry("900x700")
viewer = PageViewer(root, UIEventQueue(root))
viewer.grid(row=0, column=0, sticky="nsew")   # like library_screen: grid + sticky
root.grid_rowconfigure(0, weight=1); root.grid_columnconfigure(0, weight=1)
LOREM = "Lorem ipsum dolor sit amet. " * 80
viewer.set_document("d", "/nonexistent/x.pdf", 5, {1: LOREM})
viewer._toggle_text()
for _ in range(5): root.update_idletasks(); root.update()
pf = viewer.text_scroll._parent_frame
canvas = viewer.text_scroll._parent_canvas
tb = viewer.text_box
print("parent_frame mapped:", pf.winfo_ismapped(), "geom:", pf.winfo_geometry())
print("canvas mapped:", canvas.winfo_ismapped(), "geom:", canvas.winfo_geometry(), "req:", canvas.winfo_reqwidth(), canvas.winfo_reqheight())
print("textbox geom:", tb.winfo_geometry(), "mapped:", tb.winfo_ismapped())
print("inner text len:", len(tb._textbox.get('1.0','end').strip()))
root.destroy()
