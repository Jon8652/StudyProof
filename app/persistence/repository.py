"""JSON repository: atomic save/load, startup recovery, manual load (Part C).

All file I/O for the app lives here. The GUI and core layers never read or
write JSON directly (ARCH-3).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from app.constants import SAVE_SIZE_WARN_BYTES, SCHEMA_VERSION
from app.core.models import Dataset, Note, Session
from app.persistence import backup as backup_ops
from app.persistence.schema import (
    CorruptFileError,
    FRIENDLY_MESSAGES,
    FutureSchemaError,
    SaveError,
    UnreadableFileError,
)
from app.persistence.validation import validate_payload
from app.utils.date_utils import format_dt
from app.utils.paths import (
    ensure_app_data_dir,
    get_backup_path,
    get_default_save_path,
    get_temp_save_path,
)


@dataclass
class LoadOutcome:
    """Result of a startup or manual load with friendly user notices."""

    dataset: Dataset
    notice: str | None = None      # friendly message to show, if any
    recovered_from_backup: bool = False
    started_empty: bool = False
    migrated_from_v1: bool = False   # DATA-7: v1 payload upgraded in memory
    missing_binaries: int = 0        # ERR-40 / DATA-11: absent library files
    warnings: list[str] = field(default_factory=list)


# --- one-time storage heads-up (NFR-17) ---------------------------------------
# ``save_dataset`` pushes the "library text is getting large" notice here the
# first time a save exceeds 50 MB; the GUI drains it after any mutation that
# triggers an autosave.  The flag guarantees at most one notice per run.
PENDING_NOTICES: list[str] = []
_size_notice_shown: bool = False


def drain_pending_notices() -> list[str]:
    """Return and clear any queued persistence notices (GUI-safe polling)."""
    notices = list(PENDING_NOTICES)
    PENDING_NOTICES.clear()
    return notices


def dataset_to_payload(dataset: Dataset) -> dict[str, Any]:
    """Serialize a Dataset into the schema-version-2 JSON structure (§14)."""
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": format_dt(dataset.created_at or datetime.now()),
        "updated_at": format_dt(datetime.now()),
        "settings": {
            "theme": dataset.settings.theme,
            "xp_per_10_minutes": dataset.settings.xp_per_10_minutes,
            "ai": {
                "mode": dataset.settings.ai.mode,
                "base_url": dataset.settings.ai.base_url,
                "model": dataset.settings.ai.model,
                "api_key": dataset.settings.ai.api_key,   # local-only (A-28)
            },
        },
        "sessions": [
            {
                "id": s.id,
                "start_local": format_dt(s.start_local),
                "end_local": format_dt(s.end_local),
                "duration_minutes": s.duration_minutes,
                "created_at_local": format_dt(s.created_at_local),
                "source": s.source,
            }
            for s in dataset.sessions
        ],
        "notes": [
            {
                "id": n.id,
                "title": n.title,
                "body": n.body,
                "day": n.day.isoformat() if n.day else None,
                "created_at_local": format_dt(n.created_at_local),
                "updated_at_local": format_dt(n.updated_at_local),
            }
            for n in dataset.notes
        ],
        "documents": [
            {
                "id": d.id,
                "file_name": d.file_name,
                "stored_name": d.stored_name,
                "original_path": d.original_path,
                "imported_at_local": format_dt(d.imported_at_local),
                "page_count": d.page_count,
                "char_count": d.char_count,
                "status": _persisted_doc_status(d),
                "text_pages": [
                    {"page": p["page"], "text": p["text"]} for p in d.text_pages
                ],
            }
            for d in dataset.documents
        ],
        "coach_sessions": [
            {
                "id": c.id,
                "created_at_local": format_dt(c.created_at_local),
                "updated_at_local": format_dt(c.updated_at_local),
                "sources": [s.as_dict() for s in c.sources],
                "questions": [
                    {
                        "id": q.id,
                        "parent_id": q.parent_id,
                        "prompt": q.prompt,
                        "spans": q.spans,
                        "status": q.status,
                        "first_answer": q.first_answer,
                        "first_answer_at": (format_dt(q.first_answer_at)
                                            if q.first_answer_at else None),
                        "confidence": q.confidence,
                        "confidence_at": (format_dt(q.confidence_at)
                                          if q.confidence_at else None),
                        "revisions": [
                            {"text": r.get("text", ""),
                             "at": _fmt_maybe(r.get("at"))}
                            for r in q.revisions
                        ],
                        "hints_seen": q.hints_seen,
                        "feedback_given": q.feedback_given,
                        "followup_ids": q.followup_ids,
                    }
                    for q in c.questions
                ],
                "turns": [
                    {
                        "id": t.id,
                        "at_local": format_dt(t.at_local),
                        "role": t.role,
                        "kind": t.kind,
                        "question_id": t.question_id,
                        "mode": t.mode,
                        "text": t.text,
                        "confidence": t.confidence,
                        "quotes": t.quotes,
                        "ungrounded_count": t.ungrounded_count,
                    }
                    for t in c.turns
                ],
            }
            for c in dataset.coach_sessions
        ],
    }


def _fmt_maybe(value: Any) -> str | None:
    """Format a datetime if present; pass strings through untouched."""
    if isinstance(value, datetime):
        return format_dt(value)
    return value if isinstance(value, str) else None


def _persisted_doc_status(document) -> str:
    """In-memory ``extracting`` is transient — never persisted (PDF-4).

    A document that has not finished extracting keeps whatever text it has
    but persists as ``textless`` until extraction completes on a later run.
    """
    if document.status == "extracting":
        return "textless"
    return document.status


def _read_json(path: Path) -> Any:
    """Read and parse a JSON file, mapping failures to friendly errors."""
    if not path.exists():
        raise UnreadableFileError(f"File not found: {path.name}")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, PermissionError):
        raise UnreadableFileError(f"Could not read {path.name}")
    except json.JSONDecodeError:
        raise CorruptFileError(f"{path.name} is not valid.")


# --- v2 post-load mechanics ----------------------------------------------------

def _payload_declares_v1(payload: Any) -> bool:
    """True when the raw payload is an explicit schema-version-1 save."""
    if not isinstance(payload, dict):
        return False
    try:
        return int(payload.get("schema_version", 1)) == 1
    except (TypeError, ValueError):
        return False


def apply_missing_binary_status(dataset: Dataset) -> int:
    """Mark documents whose library binary is absent as ``missing``.

    Missing binaries are never corruption (DATA-10/LIB-6/ERR-40): stored
    text remains usable for grounding; only the viewer is disabled.
    Returns the number of records marked missing.
    """
    from app.persistence import library

    count = 0
    for document in dataset.documents:
        if document.status == "extracting":
            document.status = "textless"   # transient state never persisted
        if not library.binary_exists(document.stored_name):
            document.status = "missing"
            count += 1
    return count


def outcome_from_payload(payload: Any, dataset: Dataset,
                         warnings: list[str]) -> LoadOutcome:
    """Build a LoadOutcome with v2 migration/binary checks applied."""
    outcome = LoadOutcome(
        dataset=dataset,
        warnings=list(warnings),
        migrated_from_v1=_payload_declares_v1(payload),
        notice=warnings[0] if warnings else None,
    )
    missing = apply_missing_binary_status(dataset)
    outcome.missing_binaries = missing
    if missing:
        # ERR-40 / DATA-11: friendly count notice on manual load & startup.
        from app.persistence.schema import FRIENDLY_MESSAGES

        notice = FRIENDLY_MESSAGES["missing_binaries"].format(count=missing)
        outcome.warnings.append(notice)
        if outcome.notice is None:
            outcome.notice = notice
    return outcome


# --- Atomic save (FR-75) ------------------------------------------------------

def save_dataset(dataset: Dataset, path: Path | None = None) -> Path:
    """Atomically write the dataset to the default save file.

    Writes to a temp file first, then replaces the target only after a
    successful write+flush. Raises SaveError on disk failure while leaving
    in-memory state untouched (ERR-13).
    """
    target = path or get_default_save_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SaveError(FRIENDLY_MESSAGES["write_failed"]) from exc

    payload = dataset_to_payload(dataset)
    temp_path = target.with_name(target.name + ".tmp")
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target)
    except OSError as exc:
        # Clean up partial temp file; original remains intact.
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError:
            pass
        raise SaveError(FRIENDLY_MESSAGES["write_failed"]) from exc

    dataset.updated_at = datetime.now()

    # NFR-17: one-time friendly heads-up when the save grows past 50 MB.
    global _size_notice_shown
    if not _size_notice_shown:
        try:
            if target.stat().st_size > SAVE_SIZE_WARN_BYTES:
                _size_notice_shown = True
                PENDING_NOTICES.append(FRIENDLY_MESSAGES["save_size_large"])
        except OSError:
            pass
    return target


# --- Startup load flow (planned sequence in Part C) ---------------------------

def load_startup() -> LoadOutcome:
    """Load default save with automatic backup recovery and quarantine.

    Sequence:
      1. Ensure app data directory exists.
      2. Missing default save -> create empty default data (first run).
      3. Default save loads -> use it.
      4. Default corrupt/unreadable -> try backup.
      5. Backup usable -> recover with friendly notice; quarantine bad file.
      6. Backup also unusable -> start empty, preserve corrupt files.
    """
    ensure_app_data_dir()
    save_path = get_default_save_path()
    outcome: LoadOutcome | None = None

    if not save_path.exists():
        dataset = Dataset.empty()
        first_run = LoadOutcome(dataset=dataset)
        try:
            save_dataset(dataset, save_path)
        except SaveError:
            # First-run save failure is non-fatal; app still works in memory.
            pass
        return first_run

    try:
        payload = _read_json(save_path)
        dataset, warnings = validate_payload(payload)
        outcome = outcome_from_payload(payload, dataset, warnings)
    except FutureSchemaError:
        # Never overwrite a future-schema file (FR-72 / ERR-11).
        # Raise so the GUI can decide — but startup must still continue.
        raise
    except (CorruptFileError, UnreadableFileError):
        pass

    # --- default failed: attempt backup recovery (FR-67) ----------------------
    backup_path = get_backup_path()
    backup_outcome: LoadOutcome | None = None
    if backup_path.exists():
        try:
            payload = _read_json(backup_path)
            dataset, warnings = validate_payload(payload)
            backup_outcome = outcome_from_payload(payload, dataset, warnings)
            # The recovery notice takes priority over load warnings.
            backup_outcome.notice = FRIENDLY_MESSAGES["recovered_from_backup"]
            backup_outcome.recovered_from_backup = True
        except (SaveError, ValueError):
            backup_outcome = None

    if backup_outcome is not None:
        # Preserve the corrupt default instead of silently deleting (FR-69).
        backup_ops.quarantine_file(save_path)
        return backup_outcome

    if outcome is not None:
        # Default file parsed fine — unreachable in practice, but keeps the
        # flow honest without ever touching a possibly-v2 save.
        return outcome

    # --- both unusable: start empty, preserve corrupt files (FR-68/FR-69) -----
    backup_ops.quarantine_file(save_path)
    backup_ops.quarantine_file(backup_path)
    return LoadOutcome(
        dataset=Dataset.empty(),
        notice=FRIENDLY_MESSAGES["started_empty"],
        started_empty=True,
    )


# --- Manual load support (used by Settings screen) ----------------------------

def validate_file_for_manual_load(path: Path) -> LoadOutcome:
    """Parse + validate a user-selected file without touching current data.

    Applies the v2 post-load mechanics (v1→v2 migration flag, missing-binary
    detection with the ERR-40 friendly count notice) so manual loads behave
    exactly like startup loads.  Raises SaveError subclasses with friendly
    messages when invalid; the caller keeps current data unchanged in that
    case (FR-61/FR-63).
    """
    payload = _read_json(Path(path))
    dataset, warnings = validate_payload(payload)
    return outcome_from_payload(payload, dataset, warnings)


def apply_manual_load(dataset: Dataset) -> None:
    """Backup current default save, then replace it with loaded dataset.

    Called only after the user confirmed replacement (FR-62/FR-65).
    """
    save_path = get_default_save_path()
    backup_ops.create_backup(save_path)
    save_dataset(dataset, save_path)


def save_backup_now() -> Path | None:
    """Create a last-known-good backup of the current default save."""
    return backup_ops.create_backup(get_default_save_path())
