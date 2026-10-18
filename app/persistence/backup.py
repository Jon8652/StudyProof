"""Backup handling for the persistence layer (Part C).

Maintains one last-known-good backup beside the default save file (FR-64)
and creates backups before risky overwrite operations such as manual load
(FR-65). Corrupt files are preserved/quarantined, never silently deleted
(FR-69 / ERR-8 / ERR-9).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from app.utils.paths import get_backup_path, get_quarantine_path


def create_backup(save_path: Path) -> Path | None:
    """Copy the current default save file to the backup location.

    Returns the backup path when a backup was created, else None (e.g. no
    existing save file yet). Failures are swallowed: backup problems must
    never block normal saving — the atomic write still protects the main
    file.
    """
    if not save_path.exists():
        return None
    backup_path = get_backup_path()
    try:
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(save_path, backup_path)
        return backup_path
    except OSError:
        return None


def backup_exists() -> bool:
    """True when a backup file is available for recovery."""
    return get_backup_path().is_file()


def quarantine_file(path: Path) -> Path | None:
    """Preserve an unusable file by renaming it with a .corrupt suffix.

    Returns the new path, or None when the file could not be moved (the
    original is left untouched either way — never deleted).
    """
    if not path.exists():
        return None
    target = get_quarantine_path(path)
    try:
        shutil.move(str(path), str(target))
        return target
    except OSError:
        return None
