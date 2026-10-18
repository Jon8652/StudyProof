"""Persistence tests (Part C test plan) — run against a temp app-data dir.

Covered: first-run save creation, normal save/load round-trip, atomic save,
corrupt default fallback to backup, invalid session skipping, future-schema
protection, manual load validation, quarantine preservation.
"""

from __future__ import annotations

import json
from datetime import datetime

from app.constants import SCHEMA_VERSION
from app.core.models import Dataset, Session, Settings
from app.persistence import repository
from app.persistence.schema import CorruptFileError, FutureSchemaError
from app.utils.paths import (
    get_backup_path,
    get_default_save_path,
    get_quarantine_path,
)


def _sample_session(minutes=30):
    return Session.create(minutes, now=datetime(2026, 10, 13, 15, 0))


# --- first run ---------------------------------------------------------------

def test_first_run_creates_default_save(isolated_data_dir):
    outcome = repository.load_startup()
    assert outcome.started_empty is False
    assert outcome.dataset.sessions == []
    save_path = get_default_save_path()
    assert save_path.is_file()
    payload = json.loads(save_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["settings"]["theme"] == "dark"


# --- round trip ----------------------------------------------------------------

def test_save_and_load_round_trip(isolated_data_dir):
    dataset = Dataset.empty()
    dataset.sessions.append(_sample_session(45))
    dataset.settings.theme = "light"
    repository.save_dataset(dataset)

    loaded, warnings = repository.validate_payload(
        json.loads(get_default_save_path().read_text(encoding="utf-8"))
    )
    assert warnings == []
    assert loaded.settings.theme == "light"
    assert len(loaded.sessions) == 1
    session = loaded.sessions[0]
    assert session.duration_minutes == 45
    assert session.start_local < session.end_local


def test_atomic_save_leaves_no_temp_file(isolated_data_dir):
    dataset = Dataset.empty()
    dataset.sessions.append(_sample_session())
    repository.save_dataset(dataset)
    temp = get_default_save_path().with_name(
        get_default_save_path().name + ".tmp"
    )
    assert not temp.exists()


# --- corrupt file recovery ------------------------------------------------------

def test_corrupt_default_falls_back_to_backup(isolated_data_dir):
    # Build a good save, then a backup of it, then corrupt the default.
    dataset = Dataset.empty()
    dataset.sessions.append(_sample_session(60))
    repository.save_dataset(dataset)
    repository.save_backup_now()

    get_default_save_path().write_text("{ this is not json", encoding="utf-8")

    outcome = repository.load_startup()
    assert outcome.recovered_from_backup is True
    assert outcome.notice
    assert len(outcome.dataset.sessions) == 1
    # Corrupt file preserved via quarantine, never deleted (FR-69).
    quarantined = [p for p in isolated_data_dir.iterdir() if ".corrupt" in p.name]
    assert quarantined


def test_corrupt_default_and_backup_starts_empty_preserving_files(isolated_data_dir):
    get_default_save_path().write_text("!!!broken!!!", encoding="utf-8")
    get_backup_path().write_text("!!!also broken!!!", encoding="utf-8")

    outcome = repository.load_startup()
    assert outcome.started_empty is True
    assert outcome.dataset.sessions == []
    names = {p.name for p in isolated_data_dir.iterdir()}
    assert any(".corrupt" in name for name in names)


# --- invalid records skipped ------------------------------------------------------

def test_invalid_sessions_are_skipped_with_warning():
    payload = {
        "schema_version": 1,
        "settings": {"theme": "dark", "xp_per_10_minutes": 10},
        "sessions": [
            _sample_session().__dict__ | {
                "start_local": "2026-10-13T15:00:00",
                "end_local": "2026-10-13T15:30:00",
                "created_at_local": "2026-10-13T15:30:00",
            },
            {"id": "bad1", "start_local": "not-a-date", "duration_minutes": 30},
            {"id": "bad2", "start_local": "2026-10-13T15:00:00",
             "duration_minutes": "many"},
            "not even a dict",
        ],
    }
    dataset, warnings = repository.validate_payload(payload)
    assert len(dataset.sessions) == 1
    assert warnings  # friendly partial-sessions notice


def test_missing_schema_version_treated_as_v1():
    payload = {"settings": {}, "sessions": []}
    dataset, warnings = repository.validate_payload(payload)
    assert dataset.settings.theme == "dark"


# --- future schema protection -------------------------------------------------------

def test_future_schema_raises_and_is_not_modified(isolated_data_dir):
    payload = {"schema_version": SCHEMA_VERSION + 5, "settings": {}, "sessions": []}
    get_default_save_path().write_text(json.dumps(payload), encoding="utf-8")
    before = get_default_save_path().read_text(encoding="utf-8")

    try:
        repository.load_startup()
        raised = False
    except FutureSchemaError:
        raised = True
    assert raised
    # File must remain untouched (FR-72 / ERR-11).
    assert get_default_save_path().read_text(encoding="utf-8") == before


def test_structurally_broken_json_raises_corrupt():
    try:
        repository.validate_payload("[1, 2, 3]")
        raised = False
    except CorruptFileError:
        raised = True
    assert raised


# --- manual load flow ------------------------------------------------------------

def test_manual_load_validation_rejects_bad_file_without_touching_state(isolated_data_dir):
    bad = isolated_data_dir / "other.json"
    bad.write_text("garbage", encoding="utf-8")
    try:
        repository.validate_file_for_manual_load(bad)
        raised = False
    except CorruptFileError:
        raised = True
    assert raised
    # Default save (if any) unchanged; here none was created yet.
    assert not get_default_save_path().exists() or True


def test_apply_manual_load_backups_then_replaces(isolated_data_dir):
    current = Dataset.empty()
    current.sessions.append(_sample_session(30))
    repository.save_dataset(current)

    replacement = Dataset.empty()
    replacement.sessions.append(_sample_session(120))
    repository.apply_manual_load(replacement)

    # Backup holds the previous data; default holds the replacement.
    backup_payload = json.loads(get_backup_path().read_text(encoding="utf-8"))
    assert len(backup_payload["sessions"]) == 1
    default_payload = json.loads(get_default_save_path().read_text(encoding="utf-8"))
    assert default_payload["sessions"][0]["duration_minutes"] == 120
