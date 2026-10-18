"""Application state + use-case layer (Part D).

AppState is the single coordinator between the GUI, core engines, and the
persistence repository. Screens never touch raw session storage or file I/O
directly; they call action methods and read derived views.

Includes an in-memory daily index (Part L) rebuilt on load and updated
incrementally on add/delete so calendar/stats stay responsive with 10k+
sessions.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Callable

from app.core.date_rules import sessions_for_day
from app.core.medals import (
    DayAggregate,
    build_day_aggregates,
    medal_for_minutes,
)
from app.core.models import (
    Dataset,
    MAX_NOTE_BODY_LENGTH,
    MAX_NOTE_TITLE_LENGTH,
    Note,
    Session,
    Settings,
)
from app.core.stats import StatsResult, compute_stats
from app.core.streaks import current_streak, longest_streak
from app.persistence import repository
from app.persistence.schema import SaveError


@dataclass
class ActionResult:
    """Uniform friendly result for GUI-facing operations."""

    ok: bool
    message: str | None = None      # friendly error/notice when relevant
    value: object | None = None     # payload such as created session


class AppState:
    """Central mutable application state with derived caches.

    Derived indexes maintained incrementally (Part L):
    - ``_day_index``: day -> DayAggregate totals/medal for that day
    - ``_sessions_by_day``: day -> sessions attributed to that day
    - ``_session_by_id`` / ``_note_by_id``: id -> object O(1) lookups
    - ``_note_counts_by_day``: day -> number of attached notes
    """

    def __init__(self, dataset: Dataset | None = None):
        self.dataset: Dataset = dataset or Dataset.empty()
        self._day_index: dict[date, DayAggregate] = {}
        # day -> list of sessions attributed to that day (Part L): keeps
        # add/delete index maintenance O(sessions-that-day), not O(history).
        self._sessions_by_day: dict[date, list[Session]] = {}
        self._session_by_id: dict[str, Session] = {}
        self._note_by_id: dict[str, Note] = {}
        self._note_counts_by_day: dict[date, int] = {}
        self._listeners: list[Callable[[], None]] = []
        self.dirty: bool = False
        self.rebuild_index()

    # -- listener / refresh plumbing ------------------------------------------

    def add_listener(self, callback: Callable[[], None]) -> None:
        self._listeners.append(callback)

    def _notify(self) -> None:
        for callback in self._listeners:
            try:
                callback()
            except Exception:
                # A broken listener must not corrupt app state (ERR-17).
                pass

    # -- persistence ------------------------------------------------------------

    def load_startup(self) -> repository.LoadOutcome:
        """Run the startup load flow and adopt the resulting dataset."""
        outcome = repository.load_startup()
        self.dataset = outcome.dataset
        self.dirty = False
        self.rebuild_index()
        return outcome

    def autosave(self) -> ActionResult:
        """Save current state to the default save file (FR-57).

        On disk failure, in-memory data remains usable (ERR-13).
        """
        try:
            repository.save_dataset(self.dataset)
            self.dirty = False
            return ActionResult(ok=True)
        except SaveError as exc:
            return ActionResult(ok=False, message=str(exc))

    def manual_save(self) -> ActionResult:
        """Manual Save from Settings — writes immediately (FR-59)."""
        result = self.autosave()
        if result.ok:
            self._notify()
        return result

    def prepare_manual_load(self, path: Path) -> ActionResult:
        """Validate a selected file WITHOUT changing current state (FR-61)."""
        try:
            dataset, warnings = repository.validate_file_for_manual_load(path)
        except SaveError as exc:
            return ActionResult(ok=False, message=str(exc))
        return ActionResult(ok=True, value=dataset,
                            message=warnings[0] if warnings else None)

    def commit_manual_load(self, dataset: Dataset) -> ActionResult:
        """Replace in-memory data after confirmation (FR-62/A-9/FR-65)."""
        try:
            repository.apply_manual_load(dataset)
        except SaveError as exc:
            return ActionResult(ok=False, message=str(exc))
        self.dataset = dataset
        self.dirty = False
        self.rebuild_index()
        self._notify()
        return ActionResult(ok=True)

    # -- settings ----------------------------------------------------------------

    @property
    def settings(self) -> Settings:
        return self.dataset.settings

    def set_theme(self, theme: str) -> ActionResult:
        if theme not in ("dark", "light"):
            return ActionResult(ok=False, message="Unknown theme selected.")
        self.dataset.settings.theme = theme
        self._mark_dirty_and_save()
        self._notify()
        return ActionResult(ok=True)

    # -- session mutations ---------------------------------------------------------

    def log_preset_session(self, duration_minutes: int,
                           source: str = "preset") -> ActionResult:
        """Log a preset one-tap session (FR-1)."""
        return self._log_session(duration_minutes, source)

    def log_custom_session(self, hours, minutes) -> ActionResult:
        """Validate + log a custom session (FR-2..FR-5)."""
        total, error = Session.validate_custom_inputs(hours, minutes)
        if error is not None:
            return ActionResult(ok=False, message=error)
        return self._log_session(total, "custom")

    def _log_session(self, duration_minutes: int, source: str) -> ActionResult:
        error = Session.validate_duration(duration_minutes)
        if error is not None:
            return ActionResult(ok=False, message=error)
        session = Session.create(duration_minutes=duration_minutes, source=source)
        self.dataset.sessions.append(session)
        self._index_add(session)
        self._mark_dirty_and_save()
        self._notify()
        return ActionResult(ok=True, value=session)

    def delete_session(self, session_id: str) -> ActionResult:
        """Delete a session and update every derived view (FR-13..FR-15)."""
        session = self._session_by_id.get(session_id) \
            or self.dataset.session_by_id(session_id)
        if session is None:
            return ActionResult(ok=False,
                                message="That session could not be found.")
        self.dataset.sessions.remove(session)
        self._index_remove(session)
        self._mark_dirty_and_save()
        self._notify()
        return ActionResult(ok=True)

    def _mark_dirty_and_save(self) -> None:
        self.dirty = True
        # Autosave after any data-changing action (FR-57).
        self.autosave()

    # -- note mutations ---------------------------------------------------------

    def create_note(self, title: str, body: str = "",
                    day: date | None = None) -> ActionResult:
        """Create a saved note with its own title (optionally tied to a day)."""
        title = (title or "").strip()[:MAX_NOTE_TITLE_LENGTH]
        body = (body or "")[:MAX_NOTE_BODY_LENGTH]
        if not title and not body.strip():
            return ActionResult(ok=False,
                                message="Give your note a title or some text "
                                        "before saving.")
        note = Note.create(title=title, body=body, day=day)
        self.dataset.notes.append(note)
        self._index_note_added(note)
        self._mark_dirty_and_save()
        self._notify()
        return ActionResult(ok=True, value=note)

    def update_note(self, note_id: str, title: str, body: str,
                    day: date | None = None) -> ActionResult:
        """Edit an existing note's title/body/day and autosave."""
        note = self._note_by_id.get(note_id) or self.dataset.note_by_id(note_id)
        if note is None:
            return ActionResult(ok=False,
                                message="That note could not be found.")
        title = (title or "").strip()[:MAX_NOTE_TITLE_LENGTH]
        body = (body or "")[:MAX_NOTE_BODY_LENGTH]
        if not title and not body.strip():
            return ActionResult(ok=False,
                                message="Give your note a title or some text "
                                        "before saving.")
        old_day = note.day
        note.title = title
        note.body = body
        note.day = day
        note.updated_at_local = datetime.now()
        self._index_note_moved(old_day, day)
        self._mark_dirty_and_save()
        self._notify()
        return ActionResult(ok=True, value=note)

    def delete_note(self, note_id: str) -> ActionResult:
        """Delete a note permanently and autosave."""
        note = self._note_by_id.get(note_id) or self.dataset.note_by_id(note_id)
        if note is None:
            return ActionResult(ok=False,
                                message="That note could not be found.")
        self.dataset.notes.remove(note)
        self._index_note_removed(note)
        self._mark_dirty_and_save()
        self._notify()
        return ActionResult(ok=True)

    # -- derived views --------------------------------------------------------------

    def rebuild_index(self) -> None:
        """Rebuild every derived index from the dataset (load/manual-load)."""
        self._day_index = build_day_aggregates(
            self.dataset.sessions, self.dataset.settings.xp_per_10_minutes
        )
        by_day: dict[date, list[Session]] = {}
        by_id: dict[str, Session] = {}
        for session in self.dataset.sessions:
            by_day.setdefault(session.day, []).append(session)
            by_id[session.id] = session
        self._sessions_by_day = by_day
        self._session_by_id = by_id

        notes_by_id: dict[str, Note] = {}
        note_counts: dict[date, int] = {}
        for note in self.dataset.notes:
            notes_by_id[note.id] = note
            if note.day is not None:
                note_counts[note.day] = note_counts.get(note.day, 0) + 1
        self._note_by_id = notes_by_id
        self._note_counts_by_day = note_counts

    def _index_add(self, session: Session) -> None:
        day = session.day
        rate = self.dataset.settings.xp_per_10_minutes
        xp = (session.duration_minutes // 10) * rate
        self._sessions_by_day.setdefault(day, []).append(session)
        self._session_by_id[session.id] = session
        existing = self._day_index.get(day)
        if existing is None:
            self._day_index[day] = DayAggregate(
                day=day,
                total_minutes=session.duration_minutes,
                total_xp=xp,
                medal=medal_for_minutes(session.duration_minutes),
                session_ids=(session.id,),
            )
        else:
            total_minutes = existing.total_minutes + session.duration_minutes
            self._day_index[day] = replace(
                existing,
                total_minutes=total_minutes,
                total_xp=existing.total_xp + xp,
                medal=medal_for_minutes(total_minutes),
                session_ids=existing.session_ids + (session.id,),
            )

    def _index_remove(self, session: Session) -> None:
        day = session.day
        self._session_by_id.pop(session.id, None)
        existing = self._day_index.get(day)
        if existing is None:
            return

        remaining_ids = tuple(sid for sid in existing.session_ids
                              if sid != session.id)
        day_sessions = [s for s in self._sessions_by_day.get(day, [])
                        if s.id != session.id]
        if day_sessions:
            self._sessions_by_day[day] = day_sessions
        else:
            self._sessions_by_day.pop(day, None)
        if not remaining_ids:
            del self._day_index[day]
            return
        rate = self.dataset.settings.xp_per_10_minutes
        total_minutes = sum(s.duration_minutes for s in day_sessions)
        # XP must be recomputed from the *remaining* sessions: partial
        # 10-minute blocks make XP non-additive, so simple subtraction of
        # the removed session's XP would be wrong (e.g. days of 10+10+5).
        total_xp = sum((s.duration_minutes // 10) * rate for s in day_sessions)
        self._day_index[day] = replace(
            existing,
            total_minutes=total_minutes,
            total_xp=total_xp,
            medal=medal_for_minutes(total_minutes),
            session_ids=remaining_ids,
        )

    # -- note index maintenance --------------------------------------------------

    def _index_note_added(self, note: Note) -> None:
        self._note_by_id[note.id] = note
        if note.day is not None:
            self._note_counts_by_day[note.day] = \
                self._note_counts_by_day.get(note.day, 0) + 1

    def _index_note_removed(self, note: Note) -> None:
        self._note_by_id.pop(note.id, None)
        if note.day is not None:
            count = self._note_counts_by_day.get(note.day, 0) - 1
            if count > 0:
                self._note_counts_by_day[note.day] = count
            else:
                self._note_counts_by_day.pop(note.day, None)

    def _index_note_moved(self, old_day: date | None, new_day: date | None) -> None:
        if old_day == new_day:
            return
        if old_day is not None:
            count = self._note_counts_by_day.get(old_day, 0) - 1
            if count > 0:
                self._note_counts_by_day[old_day] = count
            else:
                self._note_counts_by_day.pop(old_day, None)
        if new_day is not None:
            self._note_counts_by_day[new_day] = \
                self._note_counts_by_day.get(new_day, 0) + 1

    # -- derived views --------------------------------------------------------------

    def get_day_aggregate(self, day: date) -> DayAggregate | None:
        return self._day_index.get(day)

    def get_today_summary(self) -> DayAggregate | None:
        return self._day_index.get(date.today())

    def get_month_view_data(self, year: int, month: int) -> dict[date, DayAggregate]:
        """Return indexed aggregates for days in the given month."""
        return {
            day: agg
            for day, agg in self._day_index.items()
            if day.year == year and day.month == month
        }

    def get_day_details(self, day: date) -> dict:
        """Everything the day-details dialog needs, freshly derived."""
        sessions = sessions_for_day(self.dataset.sessions, day)
        aggregate = self._day_index.get(day)
        return {
            "day": day,
            "sessions": sessions,
            "total_minutes": aggregate.total_minutes if aggregate else 0,
            "total_xp": aggregate.total_xp if aggregate else 0,
            "medal": aggregate.medal if aggregate else None,
            "notes": self.get_notes_for_day(day),
        }

    # -- note views --------------------------------------------------------------

    def get_all_notes(self) -> list[Note]:
        """All notes, most recently updated first."""
        return sorted(self.dataset.notes,
                      key=lambda n: n.updated_at_local, reverse=True)

    def get_notes_for_day(self, day: date) -> list[Note]:
        """Notes attached to ``day``, newest first."""
        matched = [n for n in self.dataset.notes if n.day == day]
        matched.sort(key=lambda n: n.updated_at_local, reverse=True)
        return matched

    def get_note_counts_by_day(self) -> dict[date, int]:
        """day -> number of notes; used by the calendar's notes indicator."""
        counts: dict[date, int] = {}
        for note in self.dataset.notes:
            if note.day is not None:
                counts[note.day] = counts.get(note.day, 0) + 1
        return counts

    def get_stats(self) -> StatsResult:
        # Reuse the maintained daily index so stats stay fast with 10k+
        # sessions (Part L).
        return compute_stats(
            self.dataset.sessions, self.dataset.settings,
            today=date.today(), day_index=self._day_index,
            notes=self.dataset.notes,
        )
