"""Streak engine (Part B).

Rules (A-7 / FR-47..FR-49):
- A qualifying day has total study time >= 10 minutes.
- Current streak: consecutive qualifying days ending today if today
  qualifies; otherwise ending yesterday (so the streak is not shown as
  broken before the day has ended).
- Longest streak: maximum run of consecutive qualifying days in history.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable

from app.constants import STREAK_QUALIFYING_MIN_MINUTES
from app.core.date_rules import group_sessions_by_day


def qualifying_days(sessions: Iterable) -> set[date]:
    """Return the set of calendar days with >= 10 attributed minutes."""
    qualifying: set[date] = set()
    for day, day_sessions in group_sessions_by_day(sessions).items():
        total = sum(s.duration_minutes for s in day_sessions)
        if total >= STREAK_QUALIFYING_MIN_MINUTES:
            qualifying.add(day)
    return qualifying


def current_streak(sessions: Iterable, today: date | None = None,
                   qualifying: set[date] | None = None) -> int:
    """Count consecutive qualifying days ending today or yesterday (FR-48).

    ``qualifying`` may be precomputed (e.g. from the daily aggregate index)
    to avoid an extra full-history pass (Part L).
    """
    if today is None:
        today = date.today()
    if qualifying is None:
        qualifying = qualifying_days(sessions)
    if not qualifying:
        return 0

    # Choose anchor: today if it qualifies, else yesterday.
    if today in qualifying:
        cursor = today
    elif (today - timedelta(days=1)) in qualifying:
        cursor = today - timedelta(days=1)
    else:
        return 0

    streak = 0
    while cursor in qualifying:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def longest_streak(sessions: Iterable,
                   qualifying: set[date] | None = None) -> int:
    """Maximum consecutive qualifying days across all history (FR-49).

    ``qualifying`` may be precomputed (Part L).
    """
    if qualifying is None:
        qualifying = qualifying_days(sessions)
    qualifying = sorted(qualifying)
    if not qualifying:
        return 0

    best = 1
    run = 1
    for previous, current in zip(qualifying, qualifying[1:]):
        if current - previous == timedelta(days=1):
            run += 1
        else:
            run = 1
        best = max(best, run)
    return best
