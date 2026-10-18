"""Statistics engine (Part B).

Computes every required statistic from the session list alone:
- Today / week / month / lifetime XP and time (FR-38..FR-42)
- Current streak / longest streak (FR-44)
- Total sessions, active days, average daily study time (FR-45, FR-50)
- Bronze / Silver / Gold day counts using highest-medal-per-day only
  (A-11 / FR-46)

All period math uses local dates and the same start-day attribution rule.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Iterable

from app.constants import STREAK_QUALIFYING_MIN_MINUTES
from app.core.medals import BRONZE, DayAggregate, GOLD, SILVER, \
    build_day_aggregates
from app.core.models import Note, Session, Settings
from app.core.streaks import current_streak, longest_streak
from app.utils.date_utils import month_bounds, week_bounds


@dataclass
class PeriodTotals:
    minutes: int = 0
    xp: int = 0


@dataclass
class StatsResult:
    today: PeriodTotals = field(default_factory=PeriodTotals)
    week: PeriodTotals = field(default_factory=PeriodTotals)
    month: PeriodTotals = field(default_factory=PeriodTotals)
    lifetime: PeriodTotals = field(default_factory=PeriodTotals)

    current_streak_days: int = 0
    longest_streak_days: int = 0

    total_sessions: int = 0
    active_days: int = 0
    average_daily_minutes: float = 0.0

    bronze_count: int = 0
    silver_count: int = 0
    gold_count: int = 0

    total_notes: int = 0


def _sum_period(day_map: dict[date, tuple[int, int]],
                start: date, end: date) -> PeriodTotals:
    minutes = xp = 0
    for day, (m, x) in day_map.items():
        if start <= day <= end:
            minutes += m
            xp += x
    return PeriodTotals(minutes=minutes, xp=xp)


def _count_medals(aggregates: Iterable[DayAggregate]) -> tuple[int, int, int]:
    """Return (bronze, silver, gold) day counts — highest medal per day only."""
    bronze = silver = gold = 0
    for agg in aggregates:
        if agg.medal == GOLD:
            gold += 1
        elif agg.medal == SILVER:
            silver += 1
        elif agg.medal == BRONZE:
            bronze += 1
    return bronze, silver, gold


def compute_stats(
    sessions: Iterable[Session],
    settings: Settings | None = None,
    today: date | None = None,
    day_index: dict[date, DayAggregate] | None = None,
    notes: Iterable[Note] | None = None,
) -> StatsResult:
    """Compute all statistics for the given sessions.

    ``day_index`` (Part L): an already-maintained daily aggregate index may
    be supplied to skip rebuilding aggregates from the full session list.
    ``notes``: optional note collection; when provided, its size is
    reported as the total-notes statistic.
    """
    sessions = list(sessions)
    xp_rate = settings.xp_per_10_minutes if settings else 10
    if today is None:
        today = date.today()

    aggregates = day_index if day_index is not None \
        else build_day_aggregates(sessions, xp_rate)

    # Fast per-day lookup of (minutes, xp)
    day_map: dict[date, tuple[int, int]] = {
        day: (agg.total_minutes, agg.total_xp)
        for day, agg in aggregates.items()
    }

    result = StatsResult()
    result.today = _sum_period(day_map, today, today)

    week_start, week_end = week_bounds(today)
    result.week = _sum_period(day_map, week_start, week_end)

    month_start, month_end = month_bounds(today)
    result.month = _sum_period(day_map, month_start, month_end)

    total_minutes = sum(m for m, _ in day_map.values())
    total_xp = sum(x for _, x in day_map.values())
    result.lifetime = PeriodTotals(minutes=total_minutes, xp=total_xp)

    # Active day = at least one logged session attributed to that day (FR-50).
    # Streaks and active-day count derive from the aggregate index directly
    # so no extra full-history grouping passes are needed (Part L).
    qualifying = {day for day, (m, _) in day_map.items()
                  if m >= STREAK_QUALIFYING_MIN_MINUTES}
    result.current_streak_days = current_streak(sessions, today=today,
                                                qualifying=qualifying)
    result.longest_streak_days = longest_streak(sessions, qualifying=qualifying)

    result.total_sessions = len(sessions)
    result.active_days = len(day_map)
    if result.active_days:
        result.average_daily_minutes = total_minutes / result.active_days

    # Medal counts: highest medal per day only (FR-46 / A-11)
    for agg in aggregates.values():
        if agg.medal == GOLD:
            result.gold_count += 1
        elif agg.medal == SILVER:
            result.silver_count += 1
        elif agg.medal == BRONZE:
            result.bronze_count += 1

    if notes is not None:
        result.total_notes = len(list(notes))

    return result
