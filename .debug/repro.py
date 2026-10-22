"""Reproduce: stopwatch buttons missing after starting a study session."""
import sys
sys.path.insert(0, "/workspace")

import customtkinter as ctk
from app.gui.widgets.stopwatch import StopwatchWidget
from app.state.app_state import AppState

root = ctk.CTk()
root.geometry("300x600+0+0")
sidebar = ctk.CTkFrame(root, width=250)
sidebar.pack(fill="y")
sw = StopwatchWidget(sidebar, AppState())
sw.pack(side="bottom", fill="x", padx=12, pady=(16, 0))
root.update_idletasks()
root.update()


def mapped(w):
    root.update_idletasks()
    root.update()
    return (w.winfo_ismapped(), w.winfo_y(), w.winfo_height())


print("--- stopwatch mode ---")
print("card reqheight:", sw.winfo_reqheight(), "actual:", sw.winfo_height())
print("btn_row mapped,y,h:", mapped(sw._btn_row))
print("cd_row  mapped,y,h:", mapped(sw._cd_btn_row))
print("start mapped:", mapped(sw._start_btn))
print("pause mapped:", mapped(sw._pause_btn))

print("--- start countdown (study session) ---")
sw.start_countdown(60)
print("mode:", sw._mode)
print("card reqheight:", sw.winfo_reqheight(), "actual:", sw.winfo_height())
print("btn_row mapped,y,h:", mapped(sw._btn_row))
print("cd_row  mapped,y,h:", mapped(sw._cd_btn_row))
print("pause mapped:", mapped(sw._pause_btn))
print("cancel mapped:", mapped(sw._cancel_btn))

root.update_idletasks(); root.update()
cdy = sw._cd_btn_row.winfo_y() + sw._cd_btn_row.winfo_height()
card_h = sw.winfo_height()
print(f"cd row bottom edge {cdy} vs card height {card_h}: "
      f"{'VISIBLE' if cdy <= card_h else 'CLIPPED OFF'}")

sw.cancel_countdown()
root.destroy()
