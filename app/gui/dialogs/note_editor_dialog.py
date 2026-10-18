"""Note editor modal dialog.

Create or edit a single note: title, body text, and an optional calendar
day. Saving routes through AppState so the note is persisted immediately
and every screen (notes list, calendar cells, day details) refreshes via
the shared listener plumbing.
"""

from __future__ import annotations

from datetime import date

import customtkinter as ctk

from app.core.models import MAX_NOTE_BODY_LENGTH, Note
from app.gui.dialogs.message_dialog import show_error
from app.state.app_state import AppState
from app.utils.date_utils import format_date_long


class NoteEditorDialog(ctk.CTkToplevel):
    def __init__(self, master, state: AppState, note: Note | None = None,
                 default_day: date | None = None):
        super().__init__(master)
        self._state = state
        self._note = note  # None => creating a new note

        self.title("Edit note" if note else "New note")
        self.transient(master)
        self.geometry("520x460")
        self.minsize(420, 360)

        container = ctk.CTkFrame(self, corner_radius=0)
        container.pack(fill="both", expand=True, padx=10, pady=10)

        heading = format_date_long(default_day) if (
            default_day and note is None
        ) else ("Editing note" if note else "New note")
        ctk.CTkLabel(
            container, text=heading,
            font=ctk.CTkFont(size=15, weight="bold"),
        ).pack(pady=(2, 8))

        form = ctk.CTkFrame(container, fg_color="transparent")
        form.pack(fill="x", padx=6)
        form.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(form, text="Title:", anchor="w").grid(
            row=0, column=0, sticky="ew", pady=(0, 2))
        self.title_entry = ctk.CTkEntry(
            form, placeholder_text="Give this note a title",
            font=ctk.CTkFont(size=14),
        )
        self.title_entry.grid(row=1, column=0, sticky="ew", pady=(0, 10))

        ctk.CTkLabel(form, text="Notes:", anchor="w").grid(
            row=2, column=0, sticky="ew", pady=(0, 2))
        self.body_text = ctk.CTkTextbox(
            form, font=ctk.CTkFont(size=13), wrap="word", height=200,
        )
        self.body_text.grid(row=3, column=0, sticky="nsew")
        form.grid_rowconfigure(3, weight=1)

        # Day association row.
        day_row = ctk.CTkFrame(container, fg_color="transparent")
        day_row.pack(fill="x", padx=6, pady=(10, 4))
        self.day_label = ctk.CTkLabel(
            day_row, text="", font=ctk.CTkFont(size=12),
            text_color=("gray40", "gray65"), anchor="w",
        )
        self.day_label.pack(side="left")
        ctk.CTkButton(
            day_row, text="Remove date", width=110, height=26,
            fg_color="gray30", hover_color="gray25",
            command=self._clear_day,
        ).pack(side="right")

        self._current_day: date | None = None
        if note is not None:
            self.title_entry.insert(0, note.title)
            self.body_text.insert("1.0", note.body)
            self._current_day = note.day
        elif default_day is not None:
            self._current_day = default_day
        self._update_day_label()

        buttons = ctk.CTkFrame(container, fg_color="transparent")
        buttons.pack(pady=(8, 2))
        ctk.CTkButton(buttons, text="Cancel", width=100, fg_color="gray30",
                      hover_color="gray25",
                      command=self.destroy).pack(side="left", padx=6)
        ctk.CTkButton(buttons, text="Save note", width=120,
                      command=self._save).pack(side="left", padx=6)

        self.grab_set()
        self.title_entry.focus_force()

    # ------------------------------------------------------------------

    def set_day(self, day: date | None) -> None:
        """Attach/detach the note from a calendar day (used by callers)."""
        self._current_day = day
        self._update_day_label()

    def _clear_day(self) -> None:
        self.set_day(None)

    def _update_day_label(self) -> None:
        if self._current_day is not None:
            self.day_label.configure(
                text=f"Attached to: {format_date_long(self._current_day)}")
        else:
            self.day_label.configure(text="Not attached to any day")

    def _save(self) -> None:
        title = self.title_entry.get()
        body = self.body_text.get("1.0", "end-1c")[:MAX_NOTE_BODY_LENGTH]

        if self._note is not None:
            result = self._state.update_note(
                self._note.id, title, body, day=self._current_day)
        else:
            result = self._state.create_note(title, body, day=self._current_day)

        if not result.ok:
            show_error(self, result.message or "The note could not be saved.")
            return
        self.destroy()
