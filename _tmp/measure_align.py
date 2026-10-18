from __future__ import annotations
import customtkinter as ctk
from app.state.app_state import AppState
from app.gui.screens.notes_screen import NotesScreen
from app.gui.screens.settings_screen import SettingsScreen

def inner_text_x(widget):
    if isinstance(widget, ctk.CTkLabel):
        w = widget._label
    elif isinstance(widget, ctk.CTkTextbox):
        w = widget._textbox
    elif isinstance(widget, ctk.CTkButton):
        w = widget._text_label
    else:
        w = widget
    w.update_idletasks()
    return w.winfo_rootx()

def main():
    ctk.set_appearance_mode("dark"); ctk.set_default_color_theme("blue")
    app = ctk.CTk(); app.geometry("1200x800")
    state = AppState()
    state.create_note("Alpha note", "Body text of the alpha note.")
    state.create_note("Beta note with a longer title", "Second body.")

    notes = NotesScreen(app, state)
    notes.grid(row=0, column=0, sticky="nsew")
    app.grid_rowconfigure(0, weight=1); app.grid_columnconfigure(0, weight=1)
    app.update_idletasks(); app.update()

    print("== NOTES SCREEN ==")
    lf = notes.list_frame
    header = [c for c in lf._parent_frame.winfo_children() if isinstance(c, ctk.CTkLabel)][0]
    canvas = lf._parent_canvas
    inset = canvas.winfo_rootx()
    print("canvas inset x (card+border_spacing):", inset)
    print("header 'All notes' text x           :", inner_text_x(header))
    rows = [c for c in lf.winfo_children() if isinstance(c, ctk.CTkButton)]
    for i, r in enumerate(rows):
        print(f"row {i}: bg x={r.winfo_rootx()}  text x={inner_text_x(r)}")
    print("detail card left   :", notes.detail_frame.winfo_rootx())
    print("title text x       :", inner_text_x(notes.detail_title))
    print("body text x        :", inner_text_x(notes.detail_body))
    print("edit btn bg x      :", notes.edit_button.winfo_rootx(),
          " text x:", inner_text_x(notes.edit_button))

    # empty state (search with no matches)
    notes.search_entry.insert(0, "zzz-no-match")
    notes.refresh(); app.update_idletasks(); app.update()
    lbls = [c for c in lf.winfo_children() if isinstance(c, ctk.CTkLabel)]
    for l in lbls:
        print("empty-label text x :", inner_text_x(l), repr(l._label.cget("text"))[:40])
    notes.search_entry.delete(0, "end"); notes.refresh(); app.update()

    settings = SettingsScreen(app, state)
    settings.grid(row=0, column=0, sticky="nsew")
    app.update_idletasks(); app.update()

    print("\n== SETTINGS SCREEN ==")
    secs = [c for c in settings.content.winfo_children() if isinstance(c, ctk.CTkFrame)]
    for sec in secs:
        head = sec.winfo_children()[0]
        title = None
        for ch in head.winfo_children():
            if isinstance(ch, ctk.CTkLabel) and ch.cget("text") in ("Appearance", "Data management", "Data status"):
                title = ch
        if title is None: continue
        t = title.cget("text")
        subs = [c for c in sec.winfo_children() if isinstance(c, ctk.CTkLabel)]
        xs = {"heading": inner_text_x(title)}
        for s in subs:
            xs["subtitle"] = min(xs.get("subtitle", 9999), inner_text_x(s))
        bodies = [c for c in sec.winfo_children() if isinstance(c, ctk.CTkFrame) and c is not head]
        for b in bodies:
            for k in b.winfo_children():
                try: xs.setdefault("body", min(xs.get("body", 9999), inner_text_x(k)))
                except Exception: pass
        print(t, "->", {k: v for k, v in xs.items()})
        # status label specifically
    st = settings.status_label
    print("status label text x:", inner_text_x(st))
    app.destroy()

main()
