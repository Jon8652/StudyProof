"""GUI-facing action wrappers (Part D).

These helpers keep screens free of branching logic: each returns a simple
ActionResult that the GUI renders as a friendly message when needed.
Destructive confirmations are handled at the GUI layer before calling in.
"""

from __future__ import annotations

from pathlib import Path

from app.state.app_state import ActionResult, AppState


def log_preset(state: AppState, duration_minutes: int) -> ActionResult:
    return state.log_preset_session(duration_minutes, source="preset")


def log_custom(state: AppState, hours, minutes) -> ActionResult:
    return state.log_custom_session(hours, minutes)


def delete_session(state: AppState, session_id: str) -> ActionResult:
    """Delete after GUI-level confirmation (FR-14 / ERR-5)."""
    return state.delete_session(session_id)


def create_note(state: AppState, title: str, body: str = "",
                day=None) -> ActionResult:
    return state.create_note(title, body, day=day)


def update_note(state: AppState, note_id: str, title: str, body: str,
                day=None) -> ActionResult:
    return state.update_note(note_id, title, body, day=day)


def delete_note(state: AppState, note_id: str) -> ActionResult:
    """Delete after GUI-level confirmation."""
    return state.delete_note(note_id)


def change_theme(state: AppState, theme: str) -> ActionResult:
    return state.set_theme(theme)


def manual_save(state: AppState) -> ActionResult:
    return state.manual_save()


def manual_load(state: AppState, path: Path) -> ActionResult:
    """Validate only; caller must confirm before committing (FR-61/FR-62)."""
    return state.prepare_manual_load(path)


def commit_loaded(state: AppState, dataset) -> ActionResult:
    return state.commit_manual_load(dataset)
