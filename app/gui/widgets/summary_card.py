"""Reusable summary card widget (Parts F/H).

A rounded, bordered frame containing a big value and an optional caption —
used for every stat tile on the Stats screen.  Values can be colour-coded
per metric (blue for time, gold for XP, accent blue for streaks/medals) so
the stats grid reads at a glance in both light and dark mode.
"""

from __future__ import annotations

import customtkinter as ctk

from app.gui.theme import (
    ACCENT,
    BORDER,
    CARD_FG,
    MUTED_TEXT,
    TIME_BLUE,
    XP_GOLD,
)

# Named value colours available to cards; unknown names fall back to the
# default theme text colour.  Medal colours match the calendar tiles so
# bronze/silver/gold read consistently across the whole app.
VALUE_COLORS = {
    "time": TIME_BLUE,
    "xp": XP_GOLD,
    "accent": ACCENT,
    "bronze": ("#8a5a33", "#cd854f"),
    "silver": ("#6e7a88", "#c0ccd8"),
    "gold": ("#a6821f", "#e6c34c"),
}


class SummaryCard(ctk.CTkFrame):
    def __init__(self, master, title: str, value: str = "\u2014",
                 caption: str | None = None, width: int = 180,
                 color: str | None = None, **kwargs):
        super().__init__(
            master, corner_radius=12, width=width,
            fg_color=CARD_FG, border_width=1, border_color=BORDER, **kwargs)
        self._title = title
        self._color = color
        self.value_label = ctk.CTkLabel(
            self, text=value, font=ctk.CTkFont(size=24, weight="bold"),
            text_color=VALUE_COLORS.get(color) if color else None,
        )
        self.value_label.pack(pady=(16, 2))
        self.caption_label = ctk.CTkLabel(
            self, text=caption or title,
            font=ctk.CTkFont(size=12), text_color=MUTED_TEXT,
        )
        self.caption_label.pack(pady=(0, 14))

    def set_value(self, value: str, caption: str | None = None) -> None:
        self.value_label.configure(text=value)
        if caption is not None:
            self.caption_label.configure(text=caption)
