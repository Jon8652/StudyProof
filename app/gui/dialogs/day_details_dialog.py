"""Day-details modal dialog (Part G).

Shows the selected date, total study time, XP, medal, the session list with
per-session delete buttons, and any notes attached to that day. Deletion
requires confirmation; after any change the dialog refreshes itself and
AppState notifies all screens so calendar/home/stats/notes update
immediately (FR-15).
"""

from __future__ import annotations

from datetime import date

import customtkinter as ctk

from app.core.medals import medal_label
from app.gui.dialogs.confirm_dialog import ask_yes_no
from app.gui.dialogs.note_editor_dialog import NoteEditorDialog
from app.gui.theme import MUTED_TEXT
from app.gui.widgets.medal_badge import MedalBadge
from app.gui.widgets.session_row import SessionRow
from app.state.app_state import AppState
from app.utils.date_utils import format_date_long, format_minutes


class DayDetailsDialog(ctk.CTkToplevel):
    def __init__(self, master, state: AppState, day: date):
        super().__init__(master)
        self._state = state
        self._day = day

        self.title("Day details")
        self.transient(master)
        self.geometry("600x560")
        self.minsize(480, 420)

        container = ctk.CTkFrame(self, corner_radius=0)
        container.pack(fill="both", expand=True, padx=8, pady=8)

        self.header_label = ctk.CTkLabel(
            container, text="", font=ctk.CTkFont(size=18, weight="bold")
        )
        self.header_label.pack(pady=(10, 2))

        totals_row = ctk.CTkFrame(container, fg_color="transparent")
        totals_row.pack(pady=(2, 8))
        self.totals_label = ctk.CTkLabel(totals_row, text="", font=ctk.CTkFont(size=13))
        self.totals_label.pack(side="left", padx=(0, 12))
        self.medal_badge = MedalBadge(totals_row, None, size=22)
        self.medal_badge.pack(side="left")
        self.medal_text_label = ctk.CTkLabel(totals_row, text="", font=ctk.CTkFont(size=13))
        self.medal_text_label.pack(side="left", padx=(4, 0))

        self.list_frame = ctk.CTkScrollableFrame(container, label_text="Sessions")
        self.list_frame.pack(fill="both", expand=True, padx=8, pady=(4, 4))

        self.empty_label = ctk.CTkLabel(
            container,
            text="No study sessions were logged on this day yet.\n"
                 "Time logged today will appear here.",
            font=ctk.CTkFont(size=13),
            text_color=("gray40", "gray65"),
            justify="center",
        )

        # --- Notes for this day -------------------------------------------------
        notes_header = ctk.CTkFrame(container, fg_color="transparent")
        notes_header.pack(fill="x", padx=10, pady=(2, 2))
        ctk.CTkLabel(
            notes_header, text="Notes",
            font=ctk.CTkFont(size=14, weight="bold"), anchor="w",
        ).pack(side="left")
        ctk.CTkButton(
            notes_header, text="+ Add note", width=96, height=26,
            command=self._add_note,
        ).pack(side="right")

        self.notes_frame = ctk.CTkScrollableFrame(
            container, fg_color="transparent", height=140,
        )
        self.notes_frame.pack(fill="x", padx=8, pady=(0, 6))

        self.no_notes_label = ctk.CTkLabel(
            container,
            text="No notes for this day yet. Use “Add note” to write one.",
            font=ctk.CTkFont(size=12),
            text_color=("gray40", "gray65"),
            justify="left",
        )

        ctk.CTkButton(container, text="Close", width=100, fg_color="gray30",
                      hover_color="gray25", command=self.destroy).pack(pady=(0, 10))

        self.grab_set()
        self.refresh()

    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """Rebuild the dialog contents from freshly derived state."""
        details = self._state.get_day_details(self._day)
        sessions = details["sessions"]

        self.header_label.configure(text=format_date_long(self._day))
        self.totals_label.configure(
            text=f"{format_minutes(details['total_minutes'])}   \u00b7   "
                 f"{details['total_xp']} XP"
        )
        medal = details["medal"]
        self.medal_badge.set_medal(medal)
        self.medal_text_label.configure(
            text=f"Medal: {medal_label(medal)}" if medal else "No medal yet"
        )

        # Safe teardown: customtkinter widgets can raise AttributeError
        # inside destroy() on some versions; never let that abort refresh.
        for child in self.list_frame.winfo_children():
            try:
                child.forget()
                child.destroy()
            except Exception:
                pass

        if not sessions:
            self.empty_label.pack(pady=(0, 10))
        else:
            self.empty_label.pack_forget()
            xp_rate = self._state.settings.xp_per_10_minutes
            for session in sessions:
                SessionRow(
                    self.list_frame, session, xp_rate,
                    on_delete=self._confirm_delete,
                ).pack(fill="x", padx=4, pady=3)

        self._render_notes(details.get("notes", []))

    # --- notes ------------------------------------------------------------------

    def _render_notes(self, notes) -> None:
        for child in self.notes_frame.winfo_children():
            try:
                child.forget()
                child.destroy()
            except Exception:
                pass

        if not notes:
            self.no_notes_label.pack(pady=(0, 4))
            return
        self.no_notes_label.pack_forget()

        for note in notes:
            row = ctk.CTkFrame(self.notes_frame, corner_radius=8)
            row.pack(fill="x", padx=4, pady=3)

            preview = note.body.strip().replace("\n", " ")
            if len(preview) > 120:
                preview = preview[:117] + "…"
            # Title on the first line, the day's date underneath — both
            # left-aligned like every other note listing.  CTkLabel has no
            # per-line alignment of its own beyond `justify`, so the title
            # and date are stacked labels packed to the left edge (a single
            # centred label with an embedded date used to render the date
            # off to the side/centre for calendar-created notes).
            day_tag = note.day.strftime("%b %d") if note.day else ""
            lines = [note.display_title()]
            if day_tag:
                lines.append(day_tag)
            title_text = "\n".join(lines)
            title_label = ctk.CTkLabel(
                row, text=title_text, font=ctk.CTkFont(size=12),
                anchor="w", justify="left", wraplength=380,
            )
            title_label.pack(side="left", padx=(12, 6), pady=(8, 0), fill="x")
            if preview:
                ctk.CTkLabel(
                    row, text=preview, font=ctk.CTkFont(size=11),
                    anchor="w", justify="left", wraplength=380,
                    text_color=MUTED_TEXT,
                ).pack(side="left", padx=(0, 6), pady=(0, 8), fill="x",
                       expand=True)
            else:
                # Keep symmetric bottom padding when there is no preview.
                title_label.configure(pady=(8, 8))

            button_row = ctk.CTkFrame(row, fg_color="transparent")
            button_row.pack(side="right", padx=(6, 10))
            ctk.CTkButton(
                button_row, text="View", width=60, height=24, fg_color="gray30",
                hover_color="gray25",
                command=lambda n=note: self._view_note(n),
            ).pack(side="left", padx=(0, 4))
            ctk.CTkButton(
                button_row, text="Delete", width=64, height=24,
                fg_color="#8b3030", hover_color="#a33d3d",
                command=lambda n=note: self._confirm_delete_note(n),
            ).pack(side="left")

    def _add_note(self) -> None:
        NoteEditorDialog(self, self._state, note=None, default_day=self._day)

    def _view_note(self, note) -> None:
        NoteEditorDialog(self, self._state, note=note)

    def _confirm_delete_note(self, note) -> None:
        confirmed = ask_yes_no(
            self,
            "Delete note?",
            f"Delete “{note.display_title()}” permanently?\n\n"
            "This cannot be undone.",
            confirm_text="Delete",
            cancel_text="Keep it",
        )
        if not confirmed:
            return
        result = self._state.delete_note(note.id)
        if not result.ok:
            from app.gui.dialogs.message_dialog import show_error

            show_error(self, result.message or "That note could not be found.")
            return
        self.refresh()

    # --- sessions -----------------------------------------------------------------

    def _confirm_delete(self, session) -> None:
        confirmed = ask_yes_no(
            self,
            "Delete session?",
            f"Delete this {format_minutes(session.duration_minutes)} session "
            f"({session.start_local.strftime('%H:%M')} - "
            f"{session.end_local.strftime('%H:%M')})?\n\n"
            "Medals, streaks, and stats will update right away.",
            confirm_text="Delete",
            cancel_text="Keep it",
        )
        if not confirmed:
            return
        result = self._state.delete_session(session.id)
        if not result.ok:
            from app.gui.dialogs.message_dialog import show_error

            show_error(self, result.message or "That session could not be found.")
            return
        # AppState._notify() refreshes calendar/home/stats; refresh ourselves too.
        self.refresh()
