"""Home / Study Tracker screen (Part F).

- Today summary card: total study time, XP earned, current medal status,
  and a progress bar toward the next medal.
- Quick-start preset buttons: 1 Hour / 2 Hours.  These no longer log time
  instantly (anti-cheat); they start a real-time countdown on the sidebar
  stopwatch widget, and the session is only recorded when it hits zero.
- Custom session modal with friendly validation — also starts a countdown.
- Start buttons are disabled while a countdown is already running so
  sessions cannot be stacked or spammed for free XP.
- Summary refreshes immediately after any successful recording
  (listener-driven).
"""

from __future__ import annotations

import customtkinter as ctk

from app.constants import PRESET_DURATIONS_MINUTES, PRESET_LABELS
from app.core.medals import medal_label
from app.gui.dialogs.custom_session_dialog import CustomSessionDialog
from app.gui.dialogs.message_dialog import show_error, show_message
from app.gui.screens.base_screen import BaseScreen
from app.gui.theme import (
    ACCENT,
    CARD_PAD_X,
    MUTED_TEXT,
    NEUTRAL,
    NEUTRAL_HOVER,
    NEUTRAL_TEXT,
    TIME_BLUE,
    XP_GOLD,
    card,
)
from app.gui.widgets.medal_badge import MedalBadge
from app.utils.date_utils import format_minutes

# Bronze threshold (FR-18) drives the "next medal" progress bar on Home.
BRONZE_TARGET_MINUTES = 120


class HomeScreen(BaseScreen):
    def __init__(self, master, state):
        super().__init__(master, state, title="Study Tracker")

        # --- Today summary panel -------------------------------------------
        today_card = card(self.content, corner_radius=16)
        today_card.pack(fill="x", pady=(0, 16))

        header_row = ctk.CTkFrame(today_card, fg_color="transparent")
        header_row.pack(fill="x", padx=CARD_PAD_X, pady=(14, 4))
        ctk.CTkLabel(
            header_row, text="Today", font=ctk.CTkFont(size=16, weight="bold"),
            anchor="w",
        ).pack(side="left")
        self.day_caption = ctk.CTkLabel(
            header_row, text="", font=ctk.CTkFont(size=12), text_color=MUTED_TEXT,
        )
        self.day_caption.pack(side="right")

        row = ctk.CTkFrame(today_card, fg_color="transparent")
        row.pack(fill="x", padx=CARD_PAD_X, pady=(0, 6))

        self.time_value = ctk.CTkLabel(
            row, text="0m", font=ctk.CTkFont(size=30, weight="bold"),
            text_color=TIME_BLUE,
        )
        self.time_value.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(row, text="study time", font=ctk.CTkFont(size=12),
                     text_color=MUTED_TEXT).grid(row=1, column=0, sticky="w")

        self.xp_value = ctk.CTkLabel(
            row, text="0 XP", font=ctk.CTkFont(size=30, weight="bold"),
            text_color=XP_GOLD,
        )
        self.xp_value.grid(row=0, column=1, padx=(48, 0), sticky="e")
        ctk.CTkLabel(row, text="XP earned", font=ctk.CTkFont(size=12),
                     text_color=MUTED_TEXT).grid(row=1, column=1, sticky="e")

        # --- progress toward the next medal -----------------------------------
        progress_frame = ctk.CTkFrame(today_card, fg_color="transparent")
        progress_frame.pack(fill="x", padx=CARD_PAD_X, pady=(2, 6))
        self.progress_bar = ctk.CTkProgressBar(
            progress_frame, height=8, corner_radius=4,
            progress_color=ACCENT, fg_color=("gray82", "gray25"),
        )
        self.progress_bar.pack(side="left", fill="x", expand=True, padx=(0, 12))
        self.progress_bar.set(0)
        self.progress_label = ctk.CTkLabel(
            progress_frame, text="No medal yet today", font=ctk.CTkFont(size=12),
            text_color=MUTED_TEXT, width=210, anchor="e",
        )
        self.progress_label.pack(side="left")

        medal_row = ctk.CTkFrame(today_card, fg_color="transparent")
        medal_row.pack(fill="x", padx=CARD_PAD_X, pady=(0, 14))
        self.medal_badge = MedalBadge(medal_row, None, size=22)
        self.medal_badge.pack(side="left")
        self.medal_label = ctk.CTkLabel(
            medal_row, text="", font=ctk.CTkFont(size=13),
            text_color=MUTED_TEXT,
        )
        self.medal_label.pack(side="left", padx=(6, 0))

        # --- Preset buttons ---------------------------------------------------
        ctk.CTkLabel(
            self.content, text="Quick start", font=ctk.CTkFont(size=15, weight="bold"),
            anchor="w",
        ).pack(fill="x", pady=(0, 6))

        n_presets = len(PRESET_DURATIONS_MINUTES)
        GAP = 10            # space between neighbouring preset buttons
        PRESET_H = 52       # same height as the buttons

        # Equal-size preset buttons whose right edge lines up with the
        # full-width custom button below.  A plain grid can't achieve this:
        # CTkButton's internal grid sets a column minsize equal to its
        # corner_radius (rounded-corner gutter) and tkinter splits odd pixel
        # remainders unevenly between columns (measured 327/322/327 for an
        # "equal" 3-column split).  Instead each button gets its own single-
        # column grid row; the rows share one computed integer width via
        # <Configure>, so all three are pixel-identical and end exactly at
        # the custom button's right edge at any window size.
        def _preset_row():
            row = ctk.CTkFrame(self.content, fg_color="transparent")
            row.pack(fill=None, pady=(0, 10), anchor="w")
            row.grid_propagate(False)
            row.grid_columnconfigure(0, weight=1, minsize=0)
            row.grid_rowconfigure(0, minsize=PRESET_H)
            return row

        preset_rows = [_preset_row() for _ in range(n_presets)]
        preset_buttons = []
        for i, minutes in enumerate(PRESET_DURATIONS_MINUTES):
            b = ctk.CTkButton(
                preset_rows[i],
                text=PRESET_LABELS.get(minutes, f"{minutes} minutes"),
                height=PRESET_H,
                corner_radius=12,
                font=ctk.CTkFont(size=15, weight="bold"),
                command=lambda m=minutes: self._start_preset(m),
            )
            b.grid(row=0, column=0, sticky="nsew")
            preset_buttons.append(b)
        self.preset_buttons = preset_buttons

        def _relayout_presets(_event=None):
            w = self.content.winfo_width()
            if w <= 1:
                return
            bw = max(0, (w - GAP * (n_presets - 1)) // n_presets)
            total = bw * n_presets + GAP * (n_presets - 1)
            for row in preset_rows:
                try:
                    row.configure(width=total, height=PRESET_H)
                except Exception:
                    pass
            # `total` is the largest evenly-divisible width that fits, so the
            # last button ends flush (or ≤ 2 px short) of the custom button's
            # right edge — visually aligned.

        self.content.bind("<Configure>", _relayout_presets)

        # NOTE: keep the widget reference separate from .pack(...) — pack()
        # returns None, and refresh() needs the button object to toggle its
        # state while a countdown is running.
        self.custom_button = ctk.CTkButton(
            self.content,
            text="+  Start custom session",
            height=44,
            corner_radius=12,
            fg_color=NEUTRAL,
            hover_color=NEUTRAL_HOVER,
            text_color=NEUTRAL_TEXT,
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._open_custom_dialog,
        )
        self.custom_button.pack(fill="x", pady=(2, 0))

        self.empty_hint = ctk.CTkLabel(
            self.content,
            text="Nothing tracked today yet. Start a session above to earn "
                 "study time!",
            font=ctk.CTkFont(size=13),
            text_color=MUTED_TEXT,
            wraplength=520,
            justify="left",
        )

        self.refresh()

    # ------------------------------------------------------------------------

    # -- countdown wiring ------------------------------------------------------
    # The shared StopwatchWidget (built by MainWindow) acts as the study
    # countdown; this screen only asks it to start/stop and mirrors the
    # "one active session" rule onto its own buttons.

    @property
    def _stopwatch(self):
        top = self.winfo_toplevel()
        return getattr(top, "stopwatch", None)

    def _start_countdown(self, total_minutes: int) -> None:
        """Start a real-time countdown; nothing is logged until it completes."""
        widget = self._stopwatch
        if widget is None:                       # headless/test fallback
            result = self.state.record_timer_session(total_minutes)
            if not result.ok:
                show_error(self, result.message
                           or "Could not start that session.")
            return
        if not widget.start_countdown(total_minutes):
            show_message(
                self.winfo_toplevel(), "Session already running",
                "Finish or cancel the current study session before "
                "starting a new one.")
            return
        show_message(
            self.winfo_toplevel(), "Session started",
            f"Your {format_minutes(total_minutes)} study countdown is "
            "running in the sidebar. It will be recorded automatically "
            "when it reaches zero \u2014 pause it if you step away.")
        self.refresh()

    def _start_preset(self, minutes: int) -> None:
        self._start_countdown(minutes)

    def _open_custom_dialog(self) -> None:
        CustomSessionDialog(self.winfo_toplevel(), self._start_countdown)

    def refresh(self) -> None:
        aggregate = self.state.get_today_summary()
        minutes = aggregate.total_minutes if aggregate else 0
        xp = aggregate.total_xp if aggregate else 0
        medal = aggregate.medal if aggregate else None

        self.time_value.configure(text=format_minutes(minutes))
        self.xp_value.configure(text=f"{xp} XP")
        try:
            from datetime import date

            self.day_caption.configure(
                text=date.today().strftime("%A, %d %B %Y"))
        except Exception:
            self.day_caption.configure(text="")

        # Progress bar toward the next medal (bronze threshold for v1).
        progress = min(1.0, minutes / BRONZE_TARGET_MINUTES) if minutes else 0.0
        self.progress_bar.set(progress)

        self.medal_badge.set_medal(medal)
        if medal:
            self.medal_label.configure(text=f"{medal_label(medal)} medal earned today!")
            self.progress_label.configure(text="Goal reached \u2713",
                                          text_color=XP_GOLD)
        else:
            self.medal_label.configure(text="")
            to_bronze = max(0, BRONZE_TARGET_MINUTES - minutes)
            if minutes == 0:
                self.progress_label.configure(text="No medal yet today",
                                              text_color=MUTED_TEXT)
            else:
                self.progress_label.configure(
                    text=f"{format_minutes(to_bronze)} more for Bronze",
                    text_color=MUTED_TEXT)

        if minutes == 0:
            self.empty_hint.pack(fill="x", pady=(12, 0))
        else:
            self.empty_hint.pack_forget()

        # Anti-cheat: while a countdown exists, starting another session is
        # blocked at the UI level too (the widget itself also refuses).
        busy = False
        widget = self._stopwatch
        if widget is not None:
            busy = widget.countdown_active
        for b in self.preset_buttons:
            b.configure(state="disabled" if busy else "normal")
        if self.custom_button is not None:
            self.custom_button.configure(
                state="disabled" if busy else "normal")
