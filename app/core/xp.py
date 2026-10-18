"""XP engine (Part B).

Rules:
- XP rate = settings.xp_per_10_minutes (default 10) — FR-9.
- Only *completed* 10-minute blocks award XP; partial blocks are discarded
  (A-1/A-2, FR-10).
- XP is always a whole number (FR-11).
- Total duration still counts toward medals even when too short for XP
  (FR-12) — handled by the medal/stats engines, not here.
"""

from __future__ import annotations

from app.constants import DEFAULT_XP_PER_10_MINUTES, XP_BLOCK_MINUTES
from app.core.models import Session, Settings


def calculate_xp(duration_minutes: int, xp_rate: int = DEFAULT_XP_PER_10_MINUTES) -> int:
    """Return whole-number XP for a duration using the configured rate."""
    if duration_minutes < 0:
        return 0
    completed_blocks = duration_minutes // XP_BLOCK_MINUTES
    return int(completed_blocks) * int(xp_rate)


def session_xp(session: Session, settings: Settings | None = None) -> int:
    """Return XP awarded for one session under the given settings."""
    rate = settings.xp_per_10_minutes if settings else DEFAULT_XP_PER_10_MINUTES
    return calculate_xp(session.duration_minutes, rate)
