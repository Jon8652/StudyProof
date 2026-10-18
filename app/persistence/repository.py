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

from app.constants import SCHEMA_VERSION
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
    warnings: list[str] = field(default_factory=list)


def dataset_to_payload(dataset: Dataset) -> dict[str, Any]:
    """Serialize a Dataset into the v1 JSON structure (section 10)."""
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": format_dt(dataset.created_at or datetime.now()),
        "updated_at": format_dt(datetime.now()),
        "settings": {
            "theme": dataset.settings.theme,
            "xp_per_10_minutes": dataset.settings.xp_per_10_minutes,
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
    }


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

    if not save_path.exists():
        dataset = Dataset.empty()
        outcome = LoadOutcome(dataset=dataset)
        try:
            save_dataset(dataset, save_path)
        except SaveError:
            # First-run save failure is non-fatal; app still works in memory.
            pass
        return outcome

    try:
        payload = _read_json(save_path)
        dataset, warnings = validate_payload(payload)
        return LoadOutcome(dataset=dataset, warnings=warnings,
                           notice=warnings[0] if warnings else None)
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
            backup_outcome = LoadOutcome(
                dataset=dataset,
                notice=FRIENDLY_MESSAGES["recovered_from_backup"],
                recovered_from_backup=True,
                warnings=warnings,
            )
        except (SaveError, ValueError):
            backup_outcome = None

    if backup_outcome is not None:
        # Preserve the corrupt default instead of silently deleting (FR-69).
        backup_ops.quarantine_file(save_path)
        return backup_outcome

    # --- both unusable: start empty, preserve corrupt files (FR-68/FR-69) -----
    backup_ops.quarantine_file(save_path)
    backup_ops.quarantine_file(backup_path)
    return LoadOutcome(
        dataset=Dataset.empty(),
        notice=FRIENDLY_MESSAGES["started_empty"],
        started_empty=True,
    )


# --- Manual load support (used by Settings screen) ----------------------------

def validate_file_for_manual_load(path: Path) -> tuple[Dataset, list[str]]:
    """Parse + validate a user-selected file without touching current data.

    Raises SaveError subclasses with friendly messages when invalid; the
    caller keeps current data unchanged in that case (FR-61/FR-63).
    """
    payload = _read_json(Path(path))
    return validate_payload(payload)


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
