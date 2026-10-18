"""Day attribution rules (Part B).

A session belongs to the calendar day of its START datetime (A-3 / FR-7).
Sessions crossing midnight are NOT split; full duration and XP are assigned
to the start day (FR-8).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Iterable

from app.core.models import Session


def session_day(session: Session) -> date:
    """Return the calendar-day a session is attributed to."""
    return session.start_local.date()


def group_sessions_by_day(
    sessions: Iterable[Session],
) -> dict[date, list[Session]]:
    """Group sessions by their attributed calendar day."""
    grouped: dict[date, list[Session]] = defaultdict(list)
    for session in sessions:
        grouped[session_day(session)].append(session)
    return dict(grouped)


def sessions_for_day(sessions: Iterable[Session], day: date) -> list[Session]:
    """Return all sessions attributed to ``day``, sorted by start time."""
    matched = [s for s in sessions if session_day(s) == day]
    matched.sort(key=lambda s: s.start_local)
    return matched
