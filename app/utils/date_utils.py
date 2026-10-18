"""Local date/time helpers used across the app.

Conventions (Part A):
- All datetimes are *local naive* datetimes (no tzinfo). The app trusts the
  system clock (A-13) and never performs timezone conversion.
- Calendar-day attribution uses a ``date`` "day key" derived from the
  session's start datetime (A-3 / FR-7).
- Serialization uses ISO-style strings (DATA-4).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

DATETIME_FORMAT = "%Y-%m-%dT%H:%M:%S"


def now_local() -> datetime:
    """Current local time as a naive datetime."""
    return datetime.now()


def today_local() -> date:
    """Today's local calendar date."""
    return now_local().date()


def day_key(value: datetime | date) -> date:
    """Return the calendar-day key for a datetime or date."""
    if isinstance(value, datetime):
        return value.date()
    return value


def parse_dt(text: str) -> datetime | None:
    """Parse an ISO-style local datetime string; return None when invalid."""
    if not isinstance(text, str):
        return None
    text = text.strip()
    try:
        # Preferred: ISO format with T separator (DATA-4)
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    try:
        return datetime.strptime(text, DATETIME_FORMAT)
    except ValueError:
        return None


def format_dt(value: datetime) -> str:
    """Serialize a datetime to the stable ISO-style storage format."""
    return value.isoformat(timespec="seconds")


def format_time(value: datetime) -> str:
    """Display a time like '23:40' (24-hour local time)."""
    return value.strftime("%H:%M")


def format_date_long(value: date) -> str:
    """Display a friendly long date like 'Tuesday, October 13, 2026'."""
    return value.strftime("%A, %B %d, %Y").replace(" 0", " ")


def week_bounds(reference: date) -> tuple[date, date]:
    """Return (monday, sunday) of the week containing ``reference`` (A-6)."""
    monday = reference - timedelta(days=reference.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def month_bounds(reference: date) -> tuple[date, date]:
    """Return (first_day, last_day) of the calendar month of ``reference``."""
    first = reference.replace(day=1)
    if reference.month == 12:
        next_first = date(reference.year + 1, 1, 1)
    else:
        next_first = date(reference.year, reference.month + 1, 1)
    return first, next_first - timedelta(days=1)


def add_months(reference: date, delta: int) -> date:
    """Shift a date by whole months, clamping the day of month."""
    month_index = reference.month - 1 + delta
    year = reference.year + month_index // 12
    month = month_index % 12 + 1
    day = min(reference.day, _days_in_month(year, month))
    return date(year, month, day)


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        nxt = date(year + 1, 1, 1)
    else:
        nxt = date(year, month + 1, 1)
    return (nxt - date(year, month, 1)).days


def format_minutes(total_minutes: int) -> str:
    """Format minutes as friendly duration text, e.g. '2h 15m', '45m', '0m'."""
    total_minutes = int(total_minutes)
    if total_minutes <= 0:
        return "0m"
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"
