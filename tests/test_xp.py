"""XP engine tests (Part B recommended cases)."""

from datetime import datetime

from app.core.models import Session, Settings
from app.core.xp import calculate_xp, session_xp


def test_5_minutes_is_0_xp():
    assert calculate_xp(5) == 0


def test_10_minutes_is_10_xp():
    assert calculate_xp(10) == 10


def test_25_minutes_is_20_xp_partial_block_discarded():
    assert calculate_xp(25) == 20


def test_30_minutes_is_30_xp():
    assert calculate_xp(30) == 30


def test_1_hour_is_60_xp():
    assert calculate_xp(60) == 60


def test_2_hours_is_120_xp():
    assert calculate_xp(120) == 120


def test_2h9m_is_120_xp():
    assert calculate_xp(129) == 120


def test_custom_rate_respected():
    assert calculate_xp(30, xp_rate=5) == 15


def test_negative_duration_never_awards_xp():
    assert calculate_xp(-10) == 0


def test_session_xp_uses_settings_rate():
    session = Session.create(45, now=datetime(2026, 10, 13, 12, 0, 0))
    assert session_xp(session, Settings(xp_per_10_minutes=10)) == 40
    assert session_xp(session, Settings(xp_per_10_minutes=20)) == 80
