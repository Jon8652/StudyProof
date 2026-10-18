"""Path helpers: application data directory and save/backup file locations.

Uses ``platformdirs`` so the default save location is the per-user
application-data folder on Windows (AppData), macOS, or Linux (FR-54).
"""

from __future__ import annotations

import os
from pathlib import Path

from platformdirs import user_data_dir

from app.constants import (
    APP_NAME,
    APP_SLUG,
    BACKUP_FILE_NAME,
    SAVE_FILE_NAME,
)


# Module-level override used only by tests; None means "use platformdirs".
_OVERRIDE_DIR: Path | None = None


def set_override_data_dir(path: "str | os.PathLike[str] | None") -> None:
    """Testing hook: redirect all app-data paths to a temporary directory."""
    global _OVERRIDE_DIR
    _OVERRIDE_DIR = Path(path) if path is not None else None


def get_app_data_dir() -> Path:
    """Return the per-user application data directory for this app.

    Honors the test override when one has been set.
    """
    if _OVERRIDE_DIR is not None:
        return _OVERRIDE_DIR
    return Path(user_data_dir(appname=APP_SLUG, appauthor=False))


def ensure_app_data_dir() -> Path:
    """Create the application data directory if needed and return it."""
    directory = get_app_data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def get_default_save_path() -> Path:
    """Path of the default JSON save file inside the app data folder."""
    return get_app_data_dir() / SAVE_FILE_NAME


def get_backup_path() -> Path:
    """Path of the last-known-good backup file next to the default save."""
    return get_app_data_dir() / BACKUP_FILE_NAME


def get_temp_save_path() -> Path:
    """Temporary path used for atomic-save writes (FR-75)."""
    return get_app_data_dir() / (SAVE_FILE_NAME + ".tmp")


def get_quarantine_path(original: Path) -> Path:
    """Return a unique quarantine path preserving a corrupt file (FR-69)."""
    from app.constants import QUARANTINE_SUFFIX

    base = original.with_name(original.name + QUARANTINE_SUFFIX)
    if not base.exists():
        return base
    counter = 1
    while True:
        candidate = original.with_name(
            f"{original.name}{QUARANTINE_SUFFIX}.{counter}"
        )
        if not candidate.exists():
            return candidate
        counter += 1


# --- Resource paths (source vs frozen executable) ----------------------------

def get_resource_dir() -> Path:
    """Return the base directory containing bundled resources.

    When running from source this is the project root; when running as a
    PyInstaller-frozen executable it is ``sys._MEIPASS`` (FR-78).

    For one-folder builds PyInstaller 6.x extracts data into
    ``<exe dir>/_internal`` and sets ``_MEIPASS`` there. If that layout is
    detected but the assets folder lives next to the executable instead
    (e.g. copied there), prefer the exe-adjacent copy so both work.
    """
    import sys

    if getattr(sys, "frozen", False):
        meipass = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        exe_dir = Path(sys.executable).parent
        if not (meipass / "assets").exists() and (exe_dir / "assets").exists():
            return exe_dir
        return meipass
    # .../app/utils/paths.py -> project root is three levels up
    return Path(__file__).resolve().parent.parent.parent


def get_asset_path(filename: str) -> Path:
    """Return the full path of an asset inside the assets folder."""
    return get_resource_dir() / "assets" / filename

