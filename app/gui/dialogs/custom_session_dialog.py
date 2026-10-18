"""Custom session modal dialog (Part F).

Separate hours/minutes fields with friendly inline validation (FR-2..FR-5,
ERR-1..ERR-4). On a valid submit it calls ``on_log(total_minutes)`` which is
expected to route through AppState.log_custom_session.
"""

from __future__ import annotations

import customtkinter as ctk

from app.core.models import Session


class CustomSessionDialog(ctk.CTkToplevel):
    def __init__(self, master, on_log):
        super().__init__(master)
        self._on_log = on_log

        self.title("Log custom session")
        self.transient(master)
        self.resizable(False, False)

        frame = ctk.CTkFrame(self, corner_radius=12)
        frame.pack(fill="both", expand=True, padx=16, pady=16)

        ctk.CTkLabel(
            frame, text="Log a custom session",
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
        ctk.CTkButton(buttons, text="Log session", width=120,
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
        self.hours_entry.focus_force()

    def _submit(self) -> None:
        total, error = Session.validate_custom_inputs(
            self.hours_entry.get(), self.minutes_entry.get()
        )
        if error is not None:
            self.message_label.configure(text=error)
            return
        self._on_log(total)
        self.destroy()
