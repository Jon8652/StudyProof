"""Medal engine tests (Part B recommended boundary cases)."""

from datetime import date, datetime

from app.core.date_rules import group_sessions_by_day, session_day, sessions_for_day
from app.core.medals import (
    BRONZE,
    GOLD,
    SILVER,
    build_day_aggregates,
    medal_for_minutes,
)
from app.core.models import Session


def _session(minutes: int, day=date(2026, 10, 13), hour=9) -> Session:
    return Session.create(minutes, now=datetime(day.year, day.month, day.day, hour))


# --- thresholds -------------------------------------------------------------

def test_119_minutes_no_medal():
    assert medal_for_minutes(119) is None


def test_120_minutes_bronze():
    assert medal_for_minutes(120) == BRONZE


def test_179_minutes_bronze():
    assert medal_for_minutes(179) == BRONZE


def test_180_minutes_silver():
    assert medal_for_minutes(180) == SILVER


def test_299_minutes_silver():
    assert medal_for_minutes(299) == SILVER


def test_300_minutes_gold():
    assert medal_for_minutes(300) == GOLD


# --- aggregates from multiple sessions ---------------------------------------

def test_day_aggregate_sums_sessions_and_awards_highest_medal():
    sessions = [_session(70, hour=9), _session(60, hour=14)]
    aggregates = build_day_aggregates(sessions, xp_rate=10)
    agg = aggregates[date(2026, 10, 13)]
    assert agg.total_minutes == 130
    assert agg.total_xp == 130  # per-session floors: 7 blocks + 6 blocks = 130 XP
    assert agg.medal == BRONZE


def test_aggregate_upgrades_to_gold_at_300():
    sessions = [_session(150, hour=8), _session(150, hour=16)]
    agg = build_day_aggregates(sessions, 10)[date(2026, 10, 13)]
    assert agg.medal == GOLD


# --- midnight-crossing attribution (A-3 / FR-7 / FR-8) ------------------------

def test_session_ending_just_past_midnight_belongs_to_start_day():
    # End 00:10 on Oct 14, duration 30 -> start 23:40 Oct 13.
    session = Session.create(30, now=datetime(2026, 10, 14, 0, 10))
    assert session_day(session) == date(2026, 10, 13)


def test_2330_to_0130_session_counts_fully_to_start_day():
    session = Session.create(120, now=datetime(2026, 10, 14, 1, 30))
    assert session.start_local == datetime(2026, 10, 13, 23, 30)
    grouped = group_sessions_by_day([session])
    assert list(grouped.keys()) == [date(2026, 10, 13)]
    assert grouped[date(2026, 10, 13)][0].duration_minutes == 120
    assert sessions_for_day([session], date(2026, 10, 14)) == []
