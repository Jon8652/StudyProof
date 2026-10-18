import sys
sys.path.insert(0, "/workspace")
from unittest.mock import MagicMock
import customtkinter as ctk
from app.gui.screens.notes_screen import (
    NotesScreen, NOTE_TITLE_TEXT, NOTE_ROW_BG, NOTE_ROW_SELECTED)
from app.gui.theme import DETAIL_CARD_FG, ALL_NOTES_CHIP_LIGHT, ALL_NOTES_CHIP_TEXT
from app.gui.screens.calendar_screen import PLAIN_FG

def resolve(val, mode):
    return val[0] if isinstance(val, tuple) and mode == "light" else \
           (val[1] if isinstance(val, tuple) else val)

state = MagicMock()
state.get_all_notes.return_value = []
for mode in ("light", "dark"):
    ctk.set_appearance_mode(mode)
    root = ctk.CTk()
    ns = NotesScreen(root, state)
    label = [c for c in ns.list_frame._parent_frame.winfo_children()
             if isinstance(c, ctk.CTkLabel)][0]
    print(mode, "| chip fg:", resolve(label.cget("fg_color"), mode),
          "chip text:", resolve(label.cget("text_color"), mode))
    print(mode, "| detail card:", resolve(ns.detail_frame.cget("fg_color"), mode),
          "| textbox bg:", resolve(ns.detail_body.cget("fg_color"), mode))
    print(mode, "| title text:", resolve(NOTE_TITLE_TEXT, mode),
          "| body text:", resolve(ns.detail_body.cget("text_color"), mode))
    print(mode, "| plain row bg:", resolve(NOTE_ROW_BG, mode),
          "| selected:", resolve(NOTE_ROW_SELECTED, mode),
          "| cal tile:", resolve(PLAIN_FG, mode))
    root.destroy()
print("ALL OK")
