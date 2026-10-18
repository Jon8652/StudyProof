"""Save-file schema definition and error types (Part C)."""

from __future__ import annotations


class SaveError(Exception):
    """Base class for friendly, non-technical persistence errors."""


class CorruptFileError(SaveError):
    """The file exists but cannot be parsed as valid JSON data."""


class FutureSchemaError(SaveError):
    """The file uses a schema version newer than this app understands.

    Files with this error must never be overwritten (FR-72 / ERR-11).
    """


class UnreadableFileError(SaveError):
    """The file could not be read (permissions, missing, IO error)."""


# Friendly messages shown to users (non-technical wording)
FRIENDLY_MESSAGES = {
    "corrupt": (
        "Your study data file could not be read properly. "
        "We kept a copy of it for safekeeping."
    ),
    "recovered_from_backup": (
        "Your main data file had a problem, so we restored your most recent "
        "backup. Everything should be up to date except very recent changes."
    ),
    "started_empty": (
        "We couldn't read your saved data or backup, so we started with an "
        "empty tracker. Your original files were preserved for safekeeping."
    ),
    "future_schema": (
        "This save file was created by a newer version of Study Tracker and "
        "can't be opened here. Your current data has not been changed."
    ),
    "unreadable": (
        "That file couldn't be opened. Please check that it exists and is "
        "readable, then try again."
    ),
    "write_failed": (
        "Saving failed — your computer may be out of space or the folder may "
        "be protected. Your data is still safe in this session; please try "
        "again."
    ),
    "partial_sessions": (
        "Some study sessions could not be loaded and were skipped. The rest "
        "of your data is fine."
    ),
}
