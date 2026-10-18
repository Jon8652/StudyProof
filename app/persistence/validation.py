"""Load-time validation for save-file payloads (Part C).

Turns a raw JSON dictionary into a validated ``Dataset`` plus a list of
friendly warnings. Invalid session records are skipped where practical
(ERR-10); structurally broken files raise CorruptFileError; newer schema
versions raise FutureSchemaError (FR-72).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.constants import SCHEMA_VERSION, SOURCE_CUSTOM, SOURCE_PRESET
from app.core.models import (
    Dataset,
    MAX_NOTE_BODY_LENGTH,
    MAX_NOTE_TITLE_LENGTH,
    Note,
    Session,
    Settings,
)
from app.persistence.schema import CorruptFileError, FutureSchemaError
from app.utils.date_utils import parse_dt


def validate_payload(payload: Any) -> tuple[Dataset, list[str]]:
    """Validate a parsed JSON payload and return (dataset, warnings).

    Raises:
        CorruptFileError: the payload is not a usable v1 save structure.
        FutureSchemaError: the payload declares a newer schema version.
    """
    warnings: list[str] = []

    if not isinstance(payload, dict):
        raise CorruptFileError("Save file is not a valid data file.")

    # --- schema version (FR-70 / FR-71) --------------------------------------
    raw_version = payload.get("schema_version")
    if raw_version is None:
        version = 1  # missing version treated as v1 when otherwise valid
    else:
        try:
            version = int(raw_version)
        except (TypeError, ValueError):
            raise CorruptFileError("Save file has an unreadable version.")
        if version > SCHEMA_VERSION:
            raise FutureSchemaError(
                f"Save file uses version {version}; this app supports "
                f"version {SCHEMA_VERSION}."
            )
        if version < 1:
            version = 1

    # --- settings (FR-73 / FR-74 safe defaults) -------------------------------
    settings = Settings.sanitized(payload.get("settings"))

    # --- sessions -------------------------------------------------------------
    raw_sessions = payload.get("sessions")
    if raw_sessions is None:
        raw_sessions = []
    if not isinstance(raw_sessions, list):
        raise CorruptFileError("Save file's session list is damaged.")

    sessions: list[Session] = []
    skipped = 0
    seen_ids: set[str] = set()
    for record in raw_sessions:
        session = _parse_session(record)
        if session is None:
            skipped += 1
            continue
        if session.id in seen_ids:  # DATA-6 unique ids — skip duplicates
            skipped += 1
            continue
        seen_ids.add(session.id)
        sessions.append(session)

    if skipped:
        from app.persistence.schema import FRIENDLY_MESSAGES

        warnings.append(FRIENDLY_MESSAGES["partial_sessions"])

    # --- notes (optional section; missing/invalid entries are skipped) --------
    raw_notes = payload.get("notes")
    if raw_notes is None:
        raw_notes = []
    if not isinstance(raw_notes, list):
        raise CorruptFileError("Save file's note list is damaged.")

    notes: list[Note] = []
    notes_skipped = 0
    seen_note_ids: set[str] = set()
    for record in raw_notes:
        note = _parse_note(record)
        if note is None:
            notes_skipped += 1
            continue
        if note.id in seen_note_ids:
            notes_skipped += 1
            continue
        seen_note_ids.add(note.id)
        notes.append(note)

    if notes_skipped:
        from app.persistence.schema import FRIENDLY_MESSAGES

        if FRIENDLY_MESSAGES["partial_sessions"] not in warnings:
            warnings.append(FRIENDLY_MESSAGES["partial_sessions"])

    created_at = _parse_optional_dt(payload.get("created_at"))
    updated_at = _parse_optional_dt(payload.get("updated_at"))

    dataset = Dataset(
        settings=settings,
        sessions=sessions,
        notes=notes,
        created_at=created_at or datetime.now(),
        updated_at=updated_at,
    )
    return dataset, warnings


def _parse_optional_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    return parse_dt(value)


def _parse_session(record: Any) -> Session | None:
    """Parse one session record; return None when unusable (ERR-10)."""
    if not isinstance(record, dict):
        return None

    sid = record.get("id")
    if not isinstance(sid, str) or not sid.strip():
        return None

    start = parse_dt(record.get("start_local", ""))
    if start is None:
        return None

    try:
        duration = int(record.get("duration_minutes"))
    except (TypeError, ValueError):
        return None
    if duration <= 0 or duration > 24 * 60:
        # Reject nonsensical durations rather than crashing.
        return None

    end = parse_dt(record.get("end_local", "")) if record.get("end_local") else None
    if end is None:
        # end_local optional: derive from start + duration (DATA schema 10.4)
        from datetime import timedelta

        end = start + timedelta(minutes=duration)

    created = parse_dt(record.get("created_at_local", "")) if record.get("created_at_local") else None
    if created is None:
        created = end

    source = record.get("source")
    if source not in (SOURCE_PRESET, SOURCE_CUSTOM):
        source = SOURCE_PRESET

    try:
        return Session(
            id=sid.strip(),
            start_local=start,
            end_local=end,
            duration_minutes=duration,
            created_at_local=created,
            source=source,
        )
    except Exception:
        return None


def _parse_note(record: Any) -> Note | None:
    """Parse one note record; return None when unusable (ERR-10 style)."""
    if not isinstance(record, dict):
        return None

    nid = record.get("id")
    if not isinstance(nid, str) or not nid.strip():
        return None

    title = Note.sanitize_text(record.get("title", ""), MAX_NOTE_TITLE_LENGTH)
    body = Note.sanitize_text(record.get("body", ""), MAX_NOTE_BODY_LENGTH)

    day: date | None = None
    raw_day = record.get("day")
    if raw_day:
        # Notes store a plain ISO date ("YYYY-MM-DD"); accept datetime too.
        parsed_dt = parse_dt(raw_day)
        if parsed_dt is not None:
            day = parsed_dt.date()

    created = _parse_optional_dt(record.get("created_at_local"))
    updated = _parse_optional_dt(record.get("updated_at_local"))
    now = datetime.now()
    if created is None:
        created = updated or now
    if updated is None:
        updated = created

    try:
        return Note(
            id=nid.strip(),
            title=title,
            body=body,
            day=day,
            created_at_local=created,
            updated_at_local=updated,
        )
    except Exception:
        return None
