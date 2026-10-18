"""PDF binary library storage (Phase 1.3, A-29 / ARCH-7).

All handling of the PDF *binary files* lives here: copy-in on import,
delete on record removal, existence checks and re-linking.  The save JSON
only ever references ``stored_name``; binaries live under
``<app-data>/library/`` (DATA-10).  GUI code receives paths only — never
opens or parses these files itself (ARCH-7; parsing is ``app.core.pdfx``).
"""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

from app.constants import LIBRARY_DIR_NAME
from app.utils.paths import get_app_data_dir


def get_library_dir() -> Path:
    """Return the library folder path (created on demand, DATA-10)."""
    return get_app_data_dir() / LIBRARY_DIR_NAME


def ensure_library_dir() -> Path:
    """Create ``<app-data>/library/`` if needed and return it."""
    directory = get_library_dir()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def new_stored_name() -> str:
    """Generate the unique stored file name for a new import (A-29)."""
    return f"{uuid.uuid4()}.pdf"


def binary_path(stored_name: str) -> Path:
    """Absolute path of a stored binary (whether or not it exists)."""
    # Guard against path traversal coming from a hand-edited save file.
    safe = Path(stored_name or "").name
    return get_library_dir() / safe


def binary_exists(stored_name: str) -> bool:
    """True when the library still holds this document's binary (LIB-6)."""
    return binary_path(stored_name).is_file()


def copy_in(source: "str | os.PathLike[str]",
            stored_name: str | None = None) -> Path:
    """Copy an external PDF into the library and return its path (PDF-1/A-29).

    Raises OSError (or FileNotFoundError) on any copy failure; on failure the
    partial destination copy is removed so no orphan binary remains (PDF-5).
    """
    source_path = Path(source)
    if not source_path.is_file():
        raise FileNotFoundError(f"Source file not found: {source_path}")
    ensure_library_dir()
    destination = binary_path(stored_name or new_stored_name())
    try:
        shutil.copyfile(source_path, destination)
    except OSError:
        # Leave no partial copy behind.
        try:
            if destination.exists():
                destination.unlink()
        except OSError:
            pass
        raise
    return destination


def remove_binary(stored_name: str) -> bool:
    """Delete a stored binary; True when a file was removed (LIB-5).

    Missing files are not an error — the record simply shows `missing`.
    """
    path = binary_path(stored_name)
    try:
        if path.is_file():
            path.unlink()
            return True
    except OSError:
        return False
    return False


def adopt_binary(source: "str | os.PathLike[str]", stored_name: str) -> Path:
    """Re-link: replace the absent library binary with a user-chosen file.

    Used by the LIB-6 "Re-link…" flow after the caller has verified the
    candidate matches the record (page count).  Same safety semantics as
    :func:`copy_in`.
    """
    return copy_in(source, stored_name=stored_name)
