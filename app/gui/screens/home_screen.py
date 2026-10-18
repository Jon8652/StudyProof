"""Home / Log Study screen (Part F).

- Today summary card: total study time, XP earned, current medal status,
  and a progress bar toward the next medal.
- One-tap preset buttons: 30 minutes / 1 hour / 2 hours (FR-1).
- Custom session modal (FR-2..FR-5) with friendly validation.
- Summary refreshes immediately after any successful log (listener-driven).
"""

from __future__ import annotations

import customtkinter as ctk

from app.constants import PRESET_DURATIONS_MINUTES, PRESET_LABELS
from app.core.medals import medal_label
from app.gui.dialogs.custom_session_dialog import CustomSessionDialog
from app.gui.dialogs.message_dialog import show_error
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
        super().__init__(master, state, title="Log Study")

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
            self.content, text="Quick log", font=ctk.CTkFont(size=15, weight="bold"),
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
                command=lambda m=minutes: self._log_preset(m),
            )
            b.grid(row=0, column=0, sticky="nsew")
            preset_buttons.append(b)

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

        ctk.CTkButton(
            self.content,
            text="+  Log custom session",
            height=44,
            corner_radius=12,
            fg_color=NEUTRAL,
            hover_color=NEUTRAL_HOVER,
            text_color=NEUTRAL_TEXT,
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._open_custom_dialog,
        ).pack(fill="x", pady=(2, 0))

        self.empty_hint = ctk.CTkLabel(
            self.content,
            text="Nothing logged today yet. Tap a button above to log your "
                 "first study session!",
            font=ctk.CTkFont(size=13),
            text_color=MUTED_TEXT,
            wraplength=520,
            justify="left",
        )

        self.refresh()

    # ------------------------------------------------------------------------

    def _log_preset(self, minutes: int) -> None:
        result = self.state.log_preset_session(minutes, source="preset")
        if not result.ok:
            show_error(self, result.message or "Could not log that session.")

    def _open_custom_dialog(self) -> None:
        CustomSessionDialog(self.winfo_toplevel(), self._log_custom)

    def _log_custom(self, total_minutes: int) -> None:
        result = self.state.log_custom_session(
            total_minutes // 60, total_minutes % 60
        )
        if not result.ok:
            show_error(self, result.message or "Could not log that session.")

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
