"""Statistics screen (Part H).

Sections: period totals (today/week/month/lifetime XP + time), streaks,
medal counts, general activity, and — since v2 — a Review section (UI-24)
showing coach activity (REV-3) and the weakest topics per source (REV-1 /
REV-2). All values come from the stats engine so they always match
calendar/home derivations, and refresh on any change. Cards are
colour-coded per metric family (time = blue, XP = gold, streaks = accent
blue, medals = bronze/silver/gold) for quick visual scanning.
"""

from __future__ import annotations

import customtkinter as ctk

from app.core.stats import WEAK_SCORE_THRESHOLD
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

        # --- Review (v2, UI-24) -------------------------------------------------
        # Coach activity cards (REV-3) plus a weak-topic list (REV-1/REV-2).
        # The weak-topic rows are rebuilt on refresh because their count
        # varies with the data; the coach cards are fixed.
        coach_row = section("Coach activity")
        add_card(coach_row, "coach_sessions", "Coach sessions", color="accent")
        add_card(coach_row, "coach_answered", "Questions answered", color="accent")
        add_card(coach_row, "coach_hints", "Hints used", color="accent")
        add_card(coach_row, "coach_revisions", "Revisions requested", color="accent")
        for col in range(4):
            coach_row.grid_columnconfigure(col, weight=1)

        # Rebuildable container: destroying its children on refresh removes
        # the old weak-topic rows (and its own header), then we re-add them.
        self._weak_container = ctk.CTkFrame(self.scroll, fg_color="transparent")
        self._weak_container.pack(fill="x")
        self._weak_header_row = ctk.CTkFrame(
            self._weak_container, fg_color="transparent")
        self._weak_header_row.pack(fill="x", pady=(14, 6))
        ctk.CTkFrame(self._weak_header_row, width=4, height=16, corner_radius=2,
                     fg_color="#2b6cb0").pack(side="left", padx=(0, 8))
        ctk.CTkLabel(
            self._weak_header_row, text="Weak topics",
            font=ctk.CTkFont(size=15, weight="bold"), anchor="w",
        ).pack(side="left")
        self._weak_empty = ctk.CTkLabel(
            self._weak_container,
            text="No weak topics flagged yet \u2014 work through the Study Coach "
                 f"and sources scoring {WEAK_SCORE_THRESHOLD}+ weak points "
                 "will appear here.",
            font=ctk.CTkFont(size=13), text_color=MUTED_TEXT,
            wraplength=520, justify="left",
        )
        self._weak_empty.pack(anchor="w", pady=(2, 6))

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

        self._refresh_review()

    # --- Review section (v2, UI-24) ------------------------------------------

    def _refresh_review(self) -> None:
        """Update coach activity cards and rebuild the weak-topic rows."""
        metrics = self.state.get_review_metrics()
        activity = metrics.activity
        cards = self._cards

        cards["coach_sessions"].set_value(str(activity.sessions),
                                         "Coach sessions")
        cards["coach_answered"].set_value(
            f"{activity.questions_answered}/{activity.questions_asked}",
            "Questions answered")
        cards["coach_hints"].set_value(str(activity.hints_used), "Hints used")
        cards["coach_revisions"].set_value(str(activity.revisions),
                                           "Revisions requested")
        if not hasattr(self, '_confidence_summary'):
            self._confidence_summary = ctk.CTkLabel(self.scroll, text='', text_color=MUTED_TEXT)
            self._confidence_summary.pack(fill='x')
        counts = activity.confidence_counts
        self._confidence_summary.configure(text=(f"Confidence: Guessed {counts['low']} · "
            f"Unsure {counts['medium']} · Confident {counts['high']}"))

        # Drop everything inside the container except the header row, then
        # re-add the empty-state label / weak-topic rows.
        for child in list(self._weak_container.winfo_children()):
            if child is not self._weak_header_row:
                child.destroy()

        weak = metrics.weak_sources
        if not weak:
            self._weak_empty = ctk.CTkLabel(
                self._weak_container,
                text=("No weak topics flagged yet \u2014 work through the Study "
                      f"Coach and sources scoring {WEAK_SCORE_THRESHOLD}+ weak "
                      "points will appear here."
                      if metrics.has_coach_data else
                      "No coaching sessions yet \u2014 start the Study Coach "
                      "from the Library or Notes screen and weak topics will "
                      "appear here."),
                font=ctk.CTkFont(size=13), text_color=MUTED_TEXT,
                wraplength=520, justify="left",
            )
            self._weak_empty.pack(anchor="w", pady=(2, 6))
            return

        for entry in weak:
            row = ctk.CTkFrame(self._weak_container, corner_radius=8,
                               fg_color=("gray85", "gray17"))
            row.pack(fill="x", pady=3)
            matching = [s for s in self.state.dataset.coach_sessions
                        if any(r.id == entry.source_id for r in s.sources)]
            if matching:
                latest = max(matching, key=lambda s: s.updated_at_local)
                ctk.CTkButton(row, text='Resume', width=70,
                             command=lambda sid=latest.id: self._resume_coach(sid)).pack(side='right', padx=4)
            kind_tag = "\U0001F4C4" if entry.kind == "document" else "\U0001F4DD"
            ctk.CTkLabel(
                row, text=f"{kind_tag}  {entry.label}",
                font=ctk.CTkFont(size=13, weight="bold"), anchor="w",
            ).pack(side="left", padx=(12, 6), pady=8)

            conf_text = ""
            if entry.average_confidence is not None:
                conf_text = f"avg confidence {entry.average_confidence:.1f}/2 \u00b7 "
            ctk.CTkLabel(
                row,
                text=f"{conf_text}{entry.question_count} question"
                     f"{'s' if entry.question_count != 1 else ''}",
                font=ctk.CTkFont(size=12), text_color=MUTED_TEXT, anchor="e",
            ).pack(side="right", padx=(6, 4), pady=8)
            ctk.CTkLabel(
                row, text=f"weak points: {entry.score}",
                font=ctk.CTkFont(size=12, weight="bold"), anchor="e",
            ).pack(side="right", padx=(6, 12), pady=8)

    def _resume_coach(self, session_id):
        root = self.winfo_toplevel()
        root.show_screen('coach')
        root.screens['coach'].resume_session(session_id)
