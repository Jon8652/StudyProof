"""Medal engine (Part B).

Daily medal thresholds (inclusive, FR-17..FR-21):
- Bronze: >= 120 minutes
- Silver: >= 180 minutes
- Gold:   >= 300 minutes

Only the highest medal earned on a day counts/displays (A-11 / FR-22).
Medals are always derived from current session data (FR-24/FR-25/FR-26).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable

from app.constants import (
    BRONZE_MIN_MINUTES,
    GOLD_MIN_MINUTES,
    SILVER_MIN_MINUTES,
)
from app.core.date_rules import group_sessions_by_day, session_day
from app.core.models import Session

# Medal ordering (lowest -> highest) and display names
NONE = None
BRONZE = "bronze"
SILVER = "silver"
GOLD = "gold"

MEDAL_ORDER = {BRONZE: 1, SILVER: 2, GOLD: 3}
MEDAL_LABELS = {BRONZE: "Bronze", SILVER: "Silver", GOLD: "Gold"}
MEDAL_ASSET_KEYS = {BRONZE: "bronze_medal", SILVER: "silver_medal", GOLD: "gold_medal"}


@dataclass(frozen=True)
class DayAggregate:
    """Derived totals for one calendar day (never stored as truth)."""

    day: date
    total_minutes: int
    total_xp: int
    medal: str | None
    session_ids: tuple[str, ...]


def medal_for_minutes(total_minutes: int) -> str | None:
    """Return the highest medal key for a daily minute total (FR-21)."""
    if total_minutes >= GOLD_MIN_MINUTES:
        return GOLD
    if total_minutes >= SILVER_MIN_MINUTES:
        return SILVER
    if total_minutes >= BRONZE_MIN_MINUTES:
        return BRONZE
    return None


def medal_label(medal: str | None) -> str:
    """Friendly label for a medal key ('' when none)."""
    if not medal:
        return ""
    return MEDAL_LABELS.get(medal, "")


def higher_medal(a: str | None, b: str | None) -> str | None:
    """Return whichever of two medals is higher (None ranks lowest)."""
    rank_a = MEDAL_ORDER.get(a or "", 0)
    rank_b = MEDAL_ORDER.get(b or "", 0)
    return a if rank_a >= rank_b else b


def build_day_aggregates(
    sessions: Iterable[Session], xp_rate: int
) -> dict[date, DayAggregate]:
    """Group sessions by attributed day and compute totals + medal per day.

    This is the single derivation used by calendar, home summary, and stats,
    guaranteeing consistent values everywhere (Risk 4 mitigation).
    """
    grouped = group_sessions_by_day(sessions)
    aggregates: dict[date, DayAggregate] = {}
    for day, day_sessions in grouped.items():
        total_minutes = sum(s.duration_minutes for s in day_sessions)
        total_xp = sum(
            (s.duration_minutes // 10) * xp_rate for s in day_sessions
        )
        aggregates[day] = DayAggregate(
            day=day,
            total_minutes=total_minutes,
            total_xp=total_xp,
            medal=medal_for_minutes(total_minutes),
            session_ids=tuple(s.id for s in day_sessions),
        )
    return aggregates


def day_medal(sessions: Iterable[Session], day: date) -> str | None:
    """Convenience: medal for a specific day computed on demand."""
    total = sum(
        s.duration_minutes for s in sessions if session_day(s) == day
    )
    return medal_for_minutes(total)
