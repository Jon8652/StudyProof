"""Streak engine tests (Part B recommended cases)."""

from datetime import date, datetime, timedelta

from app.core.models import Session
from app.core.streaks import current_streak, longest_streak, qualifying_days

BASE = date(2026, 10, 13)  # a Tuesday


def _day(minutes: int, offset: int) -> Session:
    day = BASE + timedelta(days=offset)
    return Session.create(minutes, now=datetime(day.year, day.month, day.day, 10))


def test_9_minute_day_does_not_qualify():
    assert qualifying_days([_day(9, 0)]) == set()


def test_10_minute_day_qualifies():
    assert qualifying_days([_day(10, 0)]) == {BASE}


def test_gap_breaks_streak():
    # Qualifying days at -3 and -2, then a gap on -1/yesterday: even though
    # today itself qualifies, the streak cannot jump the gap (FR-47).
    sessions = [_day(30, 0), _day(30, -2), _day(30, -3)]
    assert current_streak(sessions, today=BASE) == 1


def test_current_streak_counts_back_from_today():
    sessions = [_day(30, 0), _day(30, -1), _day(30, -2)]
    assert current_streak(sessions, today=BASE) == 3


def test_current_streak_survives_when_today_not_yet_qualified():
    # Streak ending yesterday keeps counting during today (FR-48).
    sessions = [_day(30, -1), _day(30, -2)]
    assert current_streak(sessions, today=BASE) == 2


def test_current_streak_zero_when_yesterday_also_missing():
    sessions = [_day(30, -5)]
    assert current_streak(sessions, today=BASE) == 0


def test_short_today_does_not_extend_but_yesterday_anchor_works():
    # Today has only 5 minutes (non-qualifying); yesterday qualifies.
    sessions = [_day(5, 0), _day(30, -1)]
    assert current_streak(sessions, today=BASE) == 1


def test_longest_streak_finds_max_run():
    sessions = [
        _day(30, -10), _day(30, -9), _day(30, -8),   # run of 3
        _day(30, -4), _day(30, -3),                   # run of 2
        _day(30, 0),                                  # single
    ]
    assert longest_streak(sessions) == 3


def test_longest_streak_empty_history():
    assert longest_streak([]) == 0
