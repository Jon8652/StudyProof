"""Confirmation dialog helper (Parts E/K).

Used before destructive/replacement operations: session deletion (FR-14)
and manual-load replacement (FR-62). Returns True only on explicit confirm.
"""

from __future__ import annotations

import customtkinter as ctk


def ask_yes_no(parent, title: str, message: str,
               confirm_text: str = "Yes", cancel_text: str = "Cancel") -> bool:
    """Show a modal yes/no confirmation; return the user's choice."""
    result: dict[str, bool] = {"value": False}

    dialog = ctk.CTkToplevel(parent)
    dialog.title(title)
    dialog.transient(parent)
    dialog.resizable(False, False)

    frame = ctk.CTkFrame(dialog, corner_radius=12)
    frame.pack(fill="both", expand=True, padx=16, pady=16)

    ctk.CTkLabel(frame, text=title, font=ctk.CTkFont(size=16, weight="bold")).pack(
        pady=(0, 8)
    )
    ctk.CTkLabel(
        frame, text=message, wraplength=380, justify="left", font=ctk.CTkFont(size=13)
    ).pack(pady=(0, 14))

    buttons = ctk.CTkFrame(frame, fg_color="transparent")
    buttons.pack()

    def _choose(value: bool):
        result["value"] = value
        dialog.grab_release()
        dialog.destroy()

    ctk.CTkButton(buttons, text=cancel_text, width=100,
                  fg_color="gray30", hover_color="gray25",
                  command=lambda: _choose(False)).pack(side="left", padx=6)
    ctk.CTkButton(buttons, text=confirm_text, width=100,
                  command=lambda: _choose(True)).pack(side="left", padx=6)

    # Closing with the window manager counts as cancel (non-destructive).
    dialog.protocol("WM_DELETE_WINDOW", lambda: _choose(False))

    dialog.grab_set()

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

    parent.wait_window(dialog)
    return result["value"]
