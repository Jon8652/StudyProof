"""Shared message box dialog helper (Parts E/K)."""

from __future__ import annotations

import tkinter as tk

import customtkinter as ctk


def _safe_focus(dialog) -> None:
    """Give *dialog* keyboard focus, but only while it still exists.

    ``focus_force`` schedules a Tk ``focus`` command against this widget; if
    the dialog has already been destroyed by the time the deferred job runs,
    Tcl raises ``bad window path name`` and Tkinter prints an "Exception in
    Tkinter callback" traceback to the console.  Guarding with ``winfo_exists``
    keeps the terminal clean without changing any visible behaviour.
    """
    try:
        if dialog.winfo_exists():
            dialog.focus_force()
    except tk.TclError:
        pass  # dialog vanished mid-call — nothing to focus


def show_message(parent, title: str, message: str) -> None:
    """Show a friendly modal information dialog (non-technical wording)."""
    dialog = ctk.CTkToplevel(parent)
    dialog.title(title)
    dialog.transient(parent)
    try:
        dialog.grab_set()
    except tk.TclError:
        pass  # grab can fail on a not-yet-mapped window; the dialog itself
              # still works and a later interaction re-targets the grab
    dialog.resizable(False, False)

    frame = ctk.CTkFrame(dialog, corner_radius=12)
    frame.pack(fill="both", expand=True, padx=16, pady=16)

    ctk.CTkLabel(frame, text=title, font=ctk.CTkFont(size=16, weight="bold")).pack(
        pady=(0, 8)
    )
    label = ctk.CTkLabel(
        frame, text=message, wraplength=380, justify="left", font=ctk.CTkFont(size=13)
    )
    label.pack(pady=(0, 14))

    def _close():
        dialog.grab_release()
        dialog.destroy()

    ctk.CTkButton(frame, text="OK", width=100, command=_close).pack()

    dialog.update_idletasks()
    try:
        parent_x = parent.winfo_rootx()
        parent_y = parent.winfo_rooty()
        parent_w = parent.winfo_width()
        x = parent_x + (parent_w - dialog.winfo_reqwidth()) // 2
        y = parent_y + 80
        dialog.geometry(f"+{max(x, 0)}+{max(y, 0)}")
    except Exception:
        pass
    dialog.after(50, lambda: _safe_focus(dialog))


def show_error(parent, message: str, title: str = "Something went wrong") -> None:
    """Friendly error variant of the message dialog."""
    show_message(parent, title, message)
