"""Core domain models: Session, Settings, and the persisted dataset.

No GUI or persistence dependencies — this module is pure data + rules hooks.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from app.constants import (
    DEFAULT_THEME,
    DEFAULT_XP_PER_10_MINUTES,
    MAX_INPUT_HOURS,
    MAX_INPUT_MINUTES,
    MAX_SESSION_MINUTES,
    MIN_INPUT_HOURS,
    MIN_INPUT_MINUTES,
    MIN_SESSION_MINUTES,
    SOURCE_CUSTOM,
    SOURCE_PRESET,
    VALID_THEMES,
)


@dataclass
class Session:
    """A single logged study session (local naive datetimes)."""

    id: str
    start_local: datetime
    end_local: datetime
    duration_minutes: int
    created_at_local: datetime
    source: str = SOURCE_PRESET  # "preset" or "custom"

    # -- construction helpers ------------------------------------------------

    @classmethod
    def create(cls, duration_minutes: int, source: str = SOURCE_PRESET,
               now: datetime | None = None) -> "Session":
        """Create a session ending at ``now`` (FR-6).

        End datetime = current local time; start = end - duration.
        """
        end = now if now is not None else datetime.now()
        start = end - timedelta(minutes=duration_minutes)
        return cls(
            id=str(uuid.uuid4()),
            start_local=start,
            end_local=end,
            duration_minutes=int(duration_minutes),
            created_at_local=end,
            source=source,
        )

    # -- derived properties ---------------------------------------------------

    @property
    def day(self) -> date:
        """Calendar-day attribution: the day of the START datetime (A-3/FR-7)."""
        return self.start_local.date()

    # -- validation ------------------------------------------------------------

    @staticmethod
    def validate_duration(duration_minutes: int) -> str | None:
        """Return a friendly message when invalid, else None (FR-4/A-4)."""
        if not isinstance(duration_minutes, int):
            return "Please enter valid numbers for hours and minutes."
        if duration_minutes < MIN_SESSION_MINUTES:
            return f"Session must be at least {MIN_SESSION_MINUTES} minutes."
        if duration_minutes > MAX_SESSION_MINUTES:
            return "Session cannot be longer than 12 hours."
        return None

    @staticmethod
    def validate_custom_inputs(hours: Any, minutes: Any) -> tuple[int, str | None]:
        """Validate separate hours/minutes inputs (FR-3..FR-5, ERR-1..ERR-4).

        Returns ``(total_minutes, error_message_or_None)``.
        """
        try:
            hours_int = int(str(hours).strip())
            minutes_int = int(str(minutes).strip())
        except (TypeError, ValueError):
            return 0, "Please enter valid numbers for hours and minutes."

        if hours_int < MIN_INPUT_HOURS or hours_int > MAX_INPUT_HOURS:
            return 0, f"Hours must be between {MIN_INPUT_HOURS} and {MAX_INPUT_HOURS}."
        if minutes_int < MIN_INPUT_MINUTES or minutes_int > MAX_INPUT_MINUTES:
            return 0, (
                f"Minutes must be between {MIN_INPUT_MINUTES} and {MAX_INPUT_MINUTES}."
            )

        total = hours_int * 60 + minutes_int
        err = Session.validate_duration(total)
        if err is not None:
            return total, err
        return total, None


MAX_NOTE_TITLE_LENGTH = 120
MAX_NOTE_BODY_LENGTH = 100_000


@dataclass
class Note:
    """A titled free-text note, optionally attached to a calendar day.

    Notes are stored alongside sessions in the dataset so they persist with
    the normal autosave/backup flow. ``day`` is None for undated notes.
    """

    id: str
    title: str
    body: str = ""
    day: date | None = None
    created_at_local: datetime = field(default_factory=datetime.now)
    updated_at_local: datetime = field(default_factory=datetime.now)

    @classmethod
    def create(cls, title: str, body: str = "",
               day: date | None = None,
               now: datetime | None = None) -> "Note":
        moment = now if now is not None else datetime.now()
        return cls(
            id=str(uuid.uuid4()),
            title=title.strip(),
            body=body,
            day=day,
            created_at_local=moment,
            updated_at_local=moment,
        )

    @staticmethod
    def sanitize_text(value: Any, limit: int) -> str:
        """Coerce to str and clamp length; never crash on odd payloads."""
        if not isinstance(value, str):
            value = str(value or "")
        return value[:limit]

    def display_title(self) -> str:
        """Title with a friendly fallback when left blank."""
        stripped = self.title.strip()
        return stripped if stripped else "Untitled note"


@dataclass
class Settings:
    """User settings persisted in the save file (FR-73)."""

    theme: str = DEFAULT_THEME
    xp_per_10_minutes: int = DEFAULT_XP_PER_10_MINUTES

    @classmethod
    def sanitized(cls, raw: dict[str, Any] | None) -> "Settings":
        """Build settings with safe defaults for missing/invalid values (FR-74)."""
        raw = raw if isinstance(raw, dict) else {}
        theme = raw.get("theme", DEFAULT_THEME)
        if theme not in VALID_THEMES:
            theme = DEFAULT_THEME
        xp_rate = raw.get("xp_per_10_minutes", DEFAULT_XP_PER_10_MINUTES)
        try:
            xp_rate = int(xp_rate)
        except (TypeError, ValueError):
            xp_rate = DEFAULT_XP_PER_10_MINUTES
        if xp_rate < 0:
            xp_rate = DEFAULT_XP_PER_10_MINUTES
        return cls(theme=theme, xp_per_10_minutes=xp_rate)


@dataclass
class Dataset:
    """The full in-memory dataset: settings + sessions + notes (single source of truth)."""

    settings: Settings = field(default_factory=Settings)
    sessions: list[Session] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def empty(cls, now: datetime | None = None) -> "Dataset":
        moment = now if now is not None else datetime.now()
        return cls(settings=Settings(), sessions=[], notes=[], created_at=moment,
                   updated_at=moment)

    def session_by_id(self, session_id: str) -> Session | None:
        """Look up a session by id (O(n) scan; AppState keeps an id map)."""
        for session in self.sessions:
            if session.id == session_id:
                return session
        return None

    def note_by_id(self, note_id: str) -> Note | None:
        """Look up a note by id (O(n) scan; AppState keeps an id map)."""
        for note in self.notes:
            if note.id == note_id:
                return note
        return None
