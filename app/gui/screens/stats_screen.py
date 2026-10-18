"""Statistics screen (Part H).

Sections: period totals (today/week/month/lifetime XP + time), streaks,
medal counts, and general activity. All values come from the stats engine
so they always match calendar/home derivations, and refresh on any change.
Cards are colour-coded per metric family (time = blue, XP = gold, streaks
= accent blue, medals = bronze/silver/gold) for quick visual scanning.
"""

from __future__ import annotations

import customtkinter as ctk

from app.gui.screens.base_screen import BaseScreen
from app.gui.theme import CARD_PAD_X, MUTED_TEXT
from app.gui.widgets.summary_card import SummaryCard
from app.utils.date_utils import format_minutes


class StatsScreen(BaseScreen):
    def __init__(self, master, state):
        super().__init__(master, state, title="Statistics")

        self.scroll = ctk.CTkScrollableFrame(self.content, fg_color="transparent")
        # padx=CARD_PAD_X gives the card grid a symmetric inset inside the
        # content frame (which already adds SCREEN_PAD_X), so every screen —
        # Statistics included — has an equal left and right outer gap.
        self.scroll.pack(fill="both", expand=True, padx=CARD_PAD_X)

        self._cards: dict[str, SummaryCard] = {}

        def section(title: str) -> ctk.CTkFrame:
            header = ctk.CTkFrame(self.scroll, fg_color="transparent")
            header.pack(fill="x", pady=(14, 6))
            # Small accent tick before the section name for a designed look.
            ctk.CTkFrame(header, width=4, height=16, corner_radius=2,
                         fg_color="#2b6cb0").pack(side="left", padx=(0, 8))
            ctk.CTkLabel(
                header, text=title,
                font=ctk.CTkFont(size=15, weight="bold"), anchor="w",
            ).pack(side="left")
            row = ctk.CTkFrame(self.scroll, fg_color="transparent")
            row.pack(fill="x")
            return row

        def add_card(row, key: str, caption: str, col_span: int = 1,
                     color: str | None = None):
            card = SummaryCard(row, caption, color=color)
            # Symmetric half-gap (CARD_PAD_X // 2 on each side) between cards:
            # neighbours end up CARD_PAD_X apart and the outer edges line up
            # with the scroll frame's own padx, keeping left/right uniform.
            card.grid(row=0, column=len(row.grid_slaves()), columnspan=col_span,
                      padx=(CARD_PAD_X // 2, CARD_PAD_X // 2), pady=4,
                      sticky="nsew")
            self._cards[key] = card
            return card

        # --- Period totals ----------------------------------------------------
        time_row = section("Study time")
        for key in ("today_time", "week_time", "month_time", "lifetime_time"):
            add_card(time_row, key, "", color="time")
        labels = ["Today", "This week", "This month", "Lifetime"]
        for i, label in enumerate(labels):
            self._cards[list(self._cards)[i]].set_value("\u2014", label)

        xp_row = section("XP earned")
        for key in ("today_xp", "week_xp", "month_xp", "lifetime_xp"):
            add_card(xp_row, key, "", color="xp")
        for i, label in enumerate(labels):
            self._cards[list(self._cards)[4 + i]].set_value("\u2014", label)

        # --- Streaks ------------------------------------------------------------
        streak_row = section("Streaks")
        add_card(streak_row, "current_streak", "Current streak", color="accent")
        add_card(streak_row, "longest_streak", "Longest streak", color="accent")

        # --- Medals ---------------------------------------------------------------
        medal_row = section("Medal days (highest per day)")
        add_card(medal_row, "bronze", "Bronze", color="bronze")
        add_card(medal_row, "silver", "Silver", color="silver")
        add_card(medal_row, "gold", "Gold", color="gold")

        # --- General activity --------------------------------------------------
        activity_row = section("Activity")
        add_card(activity_row, "sessions", "Total sessions")
        add_card(activity_row, "active_days", "Active days")
        add_card(activity_row, "average", "Avg daily time", color="time")
        add_card(activity_row, "notes", "Total notes")

        for row_frame in (time_row, xp_row, streak_row, medal_row, activity_row):
            for col in range(4):
                row_frame.grid_columnconfigure(col, weight=1)

        self.empty_label = ctk.CTkLabel(
            self.scroll,
            text="No study data yet \u2014 log your first session on the Home "
                 "screen and stats will appear here.",
            font=ctk.CTkFont(size=13), text_color=MUTED_TEXT,
            wraplength=520, justify="left",
        )

        self.refresh()

    # ------------------------------------------------------------------------

    @staticmethod
    def _days_text(days: int) -> str:
        if days == 0:
            return "0 days"
        return f"{days} day" if days == 1 else f"{days} days"

    def refresh(self) -> None:
        stats = self.state.get_stats()
        cards = self._cards

        cards["today_time"].set_value(format_minutes(stats.today.minutes), "Today")
        cards["week_time"].set_value(format_minutes(stats.week.minutes), "This week")
        cards["month_time"].set_value(format_minutes(stats.month.minutes), "This month")
        cards["lifetime_time"].set_value(
            format_minutes(stats.lifetime.minutes), "Lifetime")

        cards["today_xp"].set_value(f"{stats.today.xp}", "Today XP")
        cards["week_xp"].set_value(f"{stats.week.xp}", "This week XP")
        cards["month_xp"].set_value(f"{stats.month.xp}", "This month XP")
        cards["lifetime_xp"].set_value(f"{stats.lifetime.xp}", "Lifetime XP")

        cards["current_streak"].set_value(self._days_text(stats.current_streak_days),
                                          "Current streak")
        cards["longest_streak"].set_value(self._days_text(stats.longest_streak_days),
                                          "Longest streak")

        cards["bronze"].set_value(str(stats.bronze_count), "Bronze days")
        cards["silver"].set_value(str(stats.silver_count), "Silver days")
        cards["gold"].set_value(str(stats.gold_count), "Gold days")

        cards["sessions"].set_value(str(stats.total_sessions), "Total sessions")
        cards["active_days"].set_value(str(stats.active_days), "Active days")
        avg = round(stats.average_daily_minutes)
        cards["average"].set_value(format_minutes(avg), "Average daily time")
        cards["notes"].set_value(str(stats.total_notes), "Total notes")

        if stats.total_sessions == 0:
            self.empty_label.pack(fill="x", pady=(8, 4))
        else:
            self.empty_label.pack_forget()
