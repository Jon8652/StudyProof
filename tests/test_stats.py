"""Statistics engine tests (Part B / Part H rules)."""

from datetime import date, datetime, timedelta

from app.core.models import Session, Settings
from app.core.stats import compute_stats
from app.utils.date_utils import month_bounds, week_bounds

# 2026-10-13 is a Tuesday; the Monday-Sunday week is Oct 12..18.
TODAY = date(2026, 10, 13)


def _session(minutes: int, day_offset: int = 0, hour: int = 10) -> Session:
    when = datetime.combine(TODAY + timedelta(days=day_offset), datetime.min.time())
    when = when.replace(hour=hour)
    return Session.create(minutes, now=when)


def test_week_bounds_are_monday_to_sunday():
    start, end = week_bounds(TODAY)
    assert start == date(2026, 10, 12)   # Monday
    assert end == date(2026, 10, 18)     # Sunday


def test_month_bounds():
    start, end = month_bounds(TODAY)
    assert start == date(2026, 10, 1)
    assert end == date(2026, 10, 31)


def test_today_totals():
    stats = compute_stats([_session(45), _session(15)], Settings(), today=TODAY)
    assert stats.today.minutes == 60
    assert stats.today.xp == 50  # 4 completed blocks + 1 block = 5 * 10 XP


def test_week_excludes_previous_week():
    sessions = [_session(60, day_offset=-1),        # Mon of same week
                _session(60, day_offset=-7)]       # previous week
    stats = compute_stats(sessions, Settings(), today=TODAY)
    assert stats.week.minutes == 60
    assert stats.lifetime.minutes == 120


def test_month_and_lifetime_xp():
    sessions = [_session(30), _session(129, day_offset=-2)]
    stats = compute_stats(sessions, Settings(), today=TODAY)
    assert stats.month.minutes == 159
    assert stats.month.xp == 150  # 3 blocks + 12 blocks... 30+120 blocks *10
    assert stats.lifetime.xp == stats.month.xp


def test_medal_counts_use_highest_medal_per_day_only():
    # One 300-minute day counts as exactly one Gold day (A-11 / FR-46).
    stats = compute_stats([_session(300)], Settings(), today=TODAY)
    assert stats.gold_count == 1
    assert stats.silver_count == 0
    assert stats.bronze_count == 0


def test_medal_counts_across_days():
    sessions = [
        _session(150, day_offset=-1),  # bronze day
        _session(200, day_offset=-2),  # silver day
        _session(350, day_offset=-3),  # gold day
        _session(30, day_offset=-4),   # no medal
    ]
    stats = compute_stats(sessions, Settings(), today=TODAY)
    assert (stats.bronze_count, stats.silver_count, stats.gold_count) == (1, 1, 1)


def test_active_days_and_average():
    sessions = [_session(60), _session(30, day_offset=-1), _session(30, day_offset=-1)]
    stats = compute_stats(sessions, Settings(), today=TODAY)
    assert stats.total_sessions == 3
    assert stats.active_days == 2
    assert stats.average_daily_minutes == 60.0


def test_streaks_reported_in_stats():
    sessions = [_session(30), _session(30, day_offset=-1)]
    stats = compute_stats(sessions, Settings(), today=TODAY)
    assert stats.current_streak_days == 2
    assert stats.longest_streak_days == 2


def test_empty_history_is_friendly_zero():
    stats = compute_stats([], Settings(), today=TODAY)
    assert stats.lifetime.minutes == 0
    assert stats.lifetime.xp == 0
    assert stats.current_streak_days == 0
    assert stats.longest_streak_days == 0
    assert stats.total_sessions == 0
    assert stats.active_days == 0
    assert stats.average_daily_minutes == 0.0


def test_custom_xp_rate_flows_into_stats():
    stats = compute_stats([_session(30)], Settings(xp_per_10_minutes=20), today=TODAY)
    assert stats.today.xp == 60


def test_total_notes_counts_supplied_notes():
    from app.core.models import Note

    notes = [Note.create(title="a", now=datetime.combine(TODAY, datetime.min.time())),
             Note.create(title="b", day=TODAY,
                         now=datetime.combine(TODAY, datetime.min.time()))]
    stats = compute_stats([_session(30)], Settings(), today=TODAY, notes=notes)
    assert stats.total_notes == 2


def test_total_notes_defaults_to_zero_without_notes_arg():
    stats = compute_stats([_session(30)], Settings(), today=TODAY)
    assert stats.total_notes == 0
