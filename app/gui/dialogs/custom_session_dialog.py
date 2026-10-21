"""Custom session modal dialog (Part F).

Separate hours/minutes fields with friendly inline validation (FR-2..FR-5,
ERR-1..ERR-4). On a valid submit it calls ``on_start(total_minutes)`` which
starts a real-time study countdown — nothing is logged until the countdown
completes (anti-cheat redesign).
"""

from __future__ import annotations

import customtkinter as ctk

from app.core.models import Session
from app.gui.dialogs._dialog_base import SafeDialog


class CustomSessionDialog(SafeDialog):
    def __init__(self, master, on_start):
        super().__init__(master)
        self._on_start = on_start

        self.title("Start custom session")
        self.transient(master)
        self.resizable(False, False)

        frame = ctk.CTkFrame(self, corner_radius=12)
        frame.pack(fill="both", expand=True, padx=16, pady=16)

        ctk.CTkLabel(
            frame, text="Start a custom session",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(pady=(0, 4))
        ctk.CTkLabel(
            frame, text="Hours: 0-12 \u00b7 Minutes: 0-59 \u00b7 5 min to 12 h total",
            font=ctk.CTkFont(size=12), text_color=("gray35", "gray70"),
        ).pack(pady=(0, 12))

        entry_row = ctk.CTkFrame(frame, fg_color="transparent")
        entry_row.pack(pady=(0, 8))

        ctk.CTkLabel(entry_row, text="Hours", font=ctk.CTkFont(size=13)).grid(
            row=0, column=0, padx=(0, 18)
        )
        ctk.CTkLabel(entry_row, text="Minutes", font=ctk.CTkFont(size=13)).grid(
            row=0, column=1
        )
        self.hours_entry = ctk.CTkEntry(entry_row, width=90, placeholder_text="e.g. 1")
        self.hours_entry.grid(row=1, column=0, pady=(4, 0), padx=(0, 18))
        self.minutes_entry = ctk.CTkEntry(entry_row, width=90, placeholder_text="e.g. 30")
        self.minutes_entry.grid(row=1, column=1, pady=(4, 0))

        # Friendly validation message area (Part F UI elements).
        self.message_label = ctk.CTkLabel(
            frame, text="", font=ctk.CTkFont(size=12),
            text_color="#d06060", wraplength=320,
        )
        self.message_label.pack(pady=(2, 6))

        buttons = ctk.CTkFrame(frame, fg_color="transparent")
        buttons.pack(pady=(0, 4))
        ctk.CTkButton(buttons, text="Cancel", width=100, fg_color="gray30",
                      hover_color="gray25", command=self.destroy).pack(
            side="left", padx=6)
        ctk.CTkButton(buttons, text="Start session", width=120,
                      command=self._submit).pack(side="left", padx=6)

        self.hours_entry.bind("<Return>", lambda _e: self._submit())
        self.minutes_entry.bind("<Return>", lambda _e: self._submit())

        self.grab_set()
        self.update_idletasks()
        try:
            x = master.winfo_rootx() + (master.winfo_width() - self.winfo_reqwidth()) // 2
            y = master.winfo_rooty() + 90
            self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        except Exception:
            pass
        self.after_idle(self._focus_hours_if_alive)

    def _focus_hours_if_alive(self) -> None:
        # Deferred via ``after_idle`` so this never runs in the middle of a
        # button click; still guarded because the dialog may be gone by then.
        try:
            if self.winfo_exists():
                self.hours_entry.focus_force()
        except ctk.TclError:
            pass  # dialog vanished mid-call — nothing to focus

    def _submit(self) -> None:
        try:
            total, error = Session.validate_custom_inputs(
                self.hours_entry.get(), self.minutes_entry.get()
            )
        except ctk.TclError:
            return  # widgets vanished mid-call
        if error is not None:
            self.message_label.configure(text=error)
            return
        # Destroy FIRST, then hand off.  ``on_start`` pops a modal "Session
        # started" message box whose CTkToplevel constructor installs an
        # app-wide ``bind_all("<Button-1>")`` focus handler; Tk can replay the
        # in-flight button press through that brand-new binding and call
        # ``focus_set()`` on this dialog's still-alive internal label before
        # we get a chance to tear it down ("bad window path name" traceback).
        # Closing us up front removes those widgets from the tree, so the
        # library's deferred focus simply finds nothing to act on.
        try:
            self.grab_release()
        except ctk.TclError:
            pass
        self.destroy()
        self._on_start(total)
