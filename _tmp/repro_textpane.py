"""Reproduce Problem 1: 'Show text' pane blank after (re)packing stages."""
import sys
sys.path.insert(0, "/workspace")
import tkinter as tk
import customtkinter as ctk
from app.gui.widgets.page_viewer import PageViewer
from app.gui.workers import UIEventQueue

root = ctk.CTk()
root.geometry("900x700")
root.update_idletasks(); root.update()

viewer = PageViewer(root, UIEventQueue(root))
viewer.pack(fill="both", expand=True, padx=10, pady=10)
root.update_idletasks(); root.update()

LOREM = ("Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 40)

# Mimic library_screen._sync_viewer for an available doc:
viewer.set_document("doc1", "/nonexistent/x.pdf", 5, {1: LOREM})
root.update_idletasks(); root.update()

print("image stage mapped:", viewer.image_canvas.winfo_ismapped())

# User clicks "Show text":
viewer._toggle_text()
root.update_idletasks(); root.update()

inner = getattr(viewer.text_box, "_textbox", viewer.text_box)
content_len = int(inner.index("end-1c").split(".")[0]) - 1
txt = inner.get("1.0", "end").strip()
canvas = viewer.text_scroll._parent_canvas
bbox = canvas.bbox("all")
print("text chars visible in tk.Text:", len(txt))
print("lines in text:", content_len)
print("scrollregion bbox right after toggle:", bbox)
print("canvas w/h right after toggle:", canvas.winfo_width(), canvas.winfo_height())
print("text_box w/h right after toggle:", viewer.text_box.winfo_width(), viewer.text_box.winfo_height())
print("yview fractions:", inner.yview())

# Let idle geometry pass run (several frames):
for _ in range(10):
    root.update_idletasks(); root.update()
    import time; time.sleep(0.02)

bbox2 = canvas.bbox("all")
print("--- after idle geometry passes ---")
print("scrollregion bbox:", bbox2)
print("canvas w/h:", canvas.winfo_width(), canvas.winfo_height())
print("text_box w/h:", viewer.text_box.winfo_width(), viewer.text_box.winfo_height())
print("yview fractions:", inner.yview())

# Now simulate the async render callback landing while text mode is on
# (worker posts _on_render_failed or placeholder repaint -> _show_stage('image'))
viewer._set_placeholder("Rendering page 1…")
root.update_idletasks(); root.update()
print("--- after stray placeholder repaint ---")
print("text_scroll packed?", bool(viewer.text_scroll._parent_frame.winfo_manager()))
print("image_canvas mapped?", viewer.image_canvas.winfo_ismapped())

# Toggle back to text twice (user pressing again)
viewer._toggle_text()
for _ in range(6):
    root.update_idletasks(); root.update()
print("--- after toggling back to text ---")
print("text still in widget:", len(inner.get("1.0","end").strip()))
print("scrollregion bbox:", canvas.bbox("all"))
print("yview:", inner.yview())
root.destroy()
