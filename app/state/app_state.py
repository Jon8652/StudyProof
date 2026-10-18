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
    SOURCE_KIND_DOCUMENT,
    SOURCE_KIND_NOTE,
    CoachSession,
    Dataset,
    Document,
    MAX_NOTE_BODY_LENGTH,
    MAX_NOTE_TITLE_LENGTH,
    Note,
    Session,
    Settings,
    SourceRef,
)
from app.core.stats import (
    ReviewMetrics,
    StatsResult,
    compute_review_metrics,
    compute_stats,
)
from app.core.streaks import current_streak, longest_streak
from app.persistence import library, repository
from app.persistence.schema import FRIENDLY_MESSAGES, SaveError

from app.constants import (   # noqa: E402  (v2 library + coach limits)
    DOC_STATUSES,
    MAX_PDF_BYTES,
    MAX_PDF_PAGES,
    MAX_SOURCES,
)


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
    v2 (schema 2) indexes:
    - ``_doc_by_id``: document id -> Document
    - ``_coach_by_id``: coach session id -> CoachSession
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
        self._doc_by_id: dict[str, Document] = {}
        self._coach_by_id: dict[str, CoachSession] = {}
        # In-memory import-size map for duplicate detection (PDF-3): the save
        # file has no size field, so freshly imported files carry their byte
        # size here for the lifetime of the run.
        self._document_sizes: dict[str, int] = {}
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
        """Validate a selected file WITHOUT changing current state (FR-61).

        Runs the full v2 post-load mechanics (v1→v2 migration, missing
        binary detection with the ERR-40 friendly count notice).  The
        resulting ``LoadOutcome`` is returned as ``value`` so the caller can
        surface warnings before asking for confirmation.
        """
        try:
            outcome = repository.validate_file_for_manual_load(path)
        except SaveError as exc:
            return ActionResult(ok=False, message=str(exc))
        return ActionResult(ok=True, value=outcome, message=outcome.notice)

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

    def drain_persistence_notices(self) -> list[str]:
        """Return one-time persistence heads-ups to surface in the GUI.

        Currently used for the NFR-17 "library text is getting large" notice,
        which ``repository.save_dataset`` queues exactly once per run.
        """
        return repository.drain_pending_notices()

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

        # v2 collections (INT-2): id indexes rebuilt from the dataset.
        docs_by_id: dict[str, Document] = {}
        for document in self.dataset.documents:
            docs_by_id[document.id] = document
            # Rebuild duplicate-detection sizes from the stored binary.
            try:
                self._document_sizes[document.id] = library.binary_path(
                    document.stored_name).stat().st_size
            except OSError:
                self._document_sizes.pop(document.id, None)
        self._doc_by_id = docs_by_id

        coach_by_id: dict[str, CoachSession] = {}
        for coach in self.dataset.coach_sessions:
            coach_by_id[coach.id] = coach
        self._coach_by_id = coach_by_id

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

    # -- v2: PDF library mutations (Phase 1.4 / INT-2) --------------------------

    def import_document(self, source_path: "str | Path",
                        file_size: int | None = None,
                        verified: bool = False) -> ActionResult:
        """Import a PDF into the library (PDF-1..PDF-3, ERR-29/ERR-30).

        The binary is copied into ``<app-data>/library/<uuid>.pdf`` *before*
        any record exists; a record is created only after a successful copy
        (PDF-1).  Duplicates are detected by ``(file_name, file_size)`` and
        refused with an "already in your library" notice plus the existing
        record id so the GUI can offer to open it (PDF-3).

        ``verified`` tells the GUI the file was already probed/opened with
        PyMuPDF (the Library import pipeline does this before calling here);
        when False (e.g. headless callers/tests), this method probes the
        file itself so unreadable or encrypted PDFs never create a record.
        """
        path = Path(source_path)
        try:
            size = path.stat().st_size if file_size is None else int(file_size)
        except OSError:
            return ActionResult(ok=False, message=FRIENDLY_MESSAGES["unreadable"])

        if size > MAX_PDF_BYTES:
            return ActionResult(ok=False, message=FRIENDLY_MESSAGES["pdf_too_large"])

        for existing in self.dataset.documents:
            if (existing.file_name.lower() == path.name.lower()
                    and self._document_sizes.get(existing.id) == size):
                return ActionResult(
                    ok=False,
                    message=FRIENDLY_MESSAGES["duplicate_import"],
                    value=existing.id,   # GUI offers "open existing record"
                )

        if not verified:
            # Probe before copying: a broken/protected PDF must never leave a
            # record behind (ERR-29 / PDF-5).
            try:
                from app.core import pdfx
                pdfx.get_page_count(path)
            except Exception as exc:
                kind = getattr(exc, "kind", None)
                if kind == "encrypted":
                    message = ("This PDF is password-protected. Remove the "
                               "password (or open it once in a PDF reader and "
                               "save an unprotected copy) and import it again.")
                elif kind == "missing":
                    message = FRIENDLY_MESSAGES["unreadable"]
                else:
                    message = FRIENDLY_MESSAGES["unreadable"]
                return ActionResult(ok=False, message=message)

        stored_name = library.new_stored_name()
        try:
            library.copy_in(path, stored_name)
        except OSError:
            # Copy failed — no record is created (PDF-1/PDF-5).
            return ActionResult(
                ok=False,
                message="This PDF couldn't be opened (it may be protected "
                        "or damaged).")

        document = Document.create(file_name=path.name,
                                   stored_name=stored_name,
                                   original_path=str(path))
        self.dataset.documents.append(document)
        self._doc_by_id[document.id] = document
        self._document_sizes[document.id] = size
        self._mark_dirty_and_save()
        self._notify()
        return ActionResult(ok=True, value=document)

    def delete_document(self, document_id: str) -> ActionResult:
        """Delete a document record + its stored binary (LIB-5).

        Coach sessions are never deleted here (COACH-12/ERR-33): affected
        sessions simply mark that source removed when they render.
        """
        document = self._doc_by_id.get(document_id) \
            or self.dataset.document_by_id(document_id)
        if document is None:
            return ActionResult(ok=False,
                                message="That document could not be found.")
        self.dataset.documents.remove(document)
        self._doc_by_id.pop(document.id, None)
        self._document_sizes.pop(document.id, None)
        library.remove_binary(document.stored_name)
        self._mark_dirty_and_save()
        self._notify()
        return ActionResult(ok=True)

    def update_document_status(self, document_id: str, status: str,
                               pages: list[tuple[int, str]] | None = None,
                               save: bool = True) -> ActionResult:
        """Apply extraction results / status transitions to a document.

        ``pages`` (when given) replaces ``text_pages`` via the model helper,
        which also derives ready/textless (PDF-4/PDF-6).  Status values are
        validated against DOC_STATUSES; unknown ones are ignored.
        """
        document = self._doc_by_id.get(document_id) \
            or self.dataset.document_by_id(document_id)
        if document is None:
            return ActionResult(ok=False,
                                message="That document could not be found.")
        if pages is not None:
            document.set_extracted_text(pages)   # derives ready/textless
        if status in DOC_STATUSES:               # "" keeps the derived state
            document.status = status
        if save:
            self._mark_dirty_and_save()
        self._notify()
        return ActionResult(ok=True, value=document)

    def relink_document(self, document_id: str,
                        source_path: "str | Path") -> ActionResult:
        """Re-link a missing binary from a user-chosen file (LIB-6).

        The caller (GUI) verifies page-count match before invoking this;
        storage adopts the file under the original ``stored_name``.
        """
        document = self._doc_by_id.get(document_id) \
            or self.dataset.document_by_id(document_id)
        if document is None:
            return ActionResult(ok=False,
                                message="That document could not be found.")
        try:
            library.adopt_binary(source_path, document.stored_name)
        except OSError:
            return ActionResult(
                ok=False,
                message="That file couldn't be used — please pick the same "
                        "PDF again.")
        document.original_path = str(Path(source_path))
        # Stored text decides usability again; keep 'missing' out of the way.
        document.status = "ready" if document.char_count > 0 else "textless"
        self._mark_dirty_and_save()
        self._notify()
        return ActionResult(ok=True, value=document)

    def get_documents(self) -> list[Document]:
        """All documents, newest import first."""
        return sorted(self.dataset.documents,
                      key=lambda d: d.imported_at_local, reverse=True)

    def pending_extraction_ids(self) -> list[str]:
        """Documents still marked ``extracting`` after a load (PDF-4).

        ``extracting`` is transient and never persisted, so a save written
        mid-extraction comes back as ``textless``; both cases mean "the
        stored text may be incomplete — re-run extraction in the background"
        (the Library screen starts an ExtractionWorker for these ids).
        """
        return [d.id for d in self.dataset.documents
                if d.status in ("extracting", "textless")
                and library.binary_exists(d.stored_name)]

    def document_by_id(self, document_id: str) -> Document | None:
        return self._doc_by_id.get(document_id) \
            or self.dataset.document_by_id(document_id)

    # -- v2: coach session containers (schema level; UI arrives in Phase 5) ----

    def create_coach_session(self, sources: list[SourceRef]) -> ActionResult:
        """Persist a new coaching container with its selection snapshot."""
        if not sources:
            return ActionResult(ok=False,
                                message="Pick at least one source first.")
        if len(sources) > MAX_SOURCES:
            return ActionResult(
                ok=False,
                message=f"A session can use up to {MAX_SOURCES} sources.")
        session = CoachSession.create(sources=sources)
        self.dataset.coach_sessions.append(session)
        self._coach_by_id[session.id] = session
        self._mark_dirty_and_save()
        self._notify()
        return ActionResult(ok=True, value=session)

    def delete_coach_session(self, coach_id: str) -> ActionResult:
        """Delete a coaching session (the GUI confirms first, COACH-14)."""
        session = self._coach_by_id.get(coach_id) \
            or self.dataset.coach_session_by_id(coach_id)
        if session is None:
            return ActionResult(ok=False,
                                message="That coach session could not be found.")
        self.dataset.coach_sessions.remove(session)
        self._coach_by_id.pop(session.id, None)
        self._mark_dirty_and_save()
        self._notify()
        return ActionResult(ok=True)

    def coach_session_by_id(self, coach_id: str) -> CoachSession | None:
        return self._coach_by_id.get(coach_id) \
            or self.dataset.coach_session_by_id(coach_id)

    def persist_coach_session(self, session: CoachSession) -> ActionResult:
        """Autosave after an in-place mutation of a coach session."""
        session.touch()
        self._mark_dirty_and_save()
        self._notify()
        return ActionResult(ok=True)

    # -- v2: source selection views (SEL-1) -------------------------------------

    def selectable_sources(self) -> list[dict]:
        """Candidate sources for the selection dialog (SEL-1/UI-25).

        Notes must be non-empty; documents must be ``ready`` (PDF-6/ERR-31 —
        textless/missing records stay listed as unavailable so the dialog can
        explain why they cannot be picked).
        """
        entries: list[dict] = []
        for note in sorted(self.dataset.notes,
                           key=lambda n: n.updated_at_local, reverse=True):
            body = (note.body or "").strip()
            if not body:
                continue
            entries.append({
                "kind": SOURCE_KIND_NOTE,
                "id": note.id,
                "label": note.display_title(),
                "chars": len(note.body),
                "available": True,
                "reason": "",
                "meta": note.day.isoformat() if note.day else "undated",
            })
        for document in self.get_documents():
            available = document.status == "ready"
            reason = ""
            if document.status == "textless":
                reason = "No selectable text (viewer still works)."
            elif document.status == "extracting":
                reason = "Still extracting — check back in a moment."
            elif document.status == "missing":
                reason = "File missing — re-link it in the Library."
            entries.append({
                "kind": SOURCE_KIND_DOCUMENT,
                "id": document.id,
                "label": document.display_label(),
                "chars": document.char_count,
                "available": available,
                "reason": reason,
                "meta": f"{document.page_count} pages",
            })
        return entries

    def source_ref_for(self, kind: str, item_id: str) -> SourceRef | None:
        """Build a selection-snapshot entry (SEL-3) for a chosen source."""
        if kind == SOURCE_KIND_NOTE:
            note = self._note_by_id.get(item_id) \
                or self.dataset.note_by_id(item_id)
            if note is None:
                return None
            return SourceRef(kind=kind, id=note.id, label=note.display_title())
        if kind == SOURCE_KIND_DOCUMENT:
            document = self.document_by_id(item_id)
            if document is None:
                return None
            return SourceRef(kind=kind, id=document.id,
                             label=document.display_label())
        return None

    def build_pack_for_selection(self, selections: list[tuple[str, str]]):
        """Assemble the SEL-2 source pack for ``(kind, id)`` selections.

        Thin wrapper over ``app.core.agent.context.build_source_pack`` so
        the dialog (and later the Coach screen) never reach into raw model
        maps themselves.  Pure derivation — no AI, no file access.
        """
        from app.core.agent.context import build_source_pack
        notes_by_id = {n.id: n for n in self.dataset.notes}
        documents_by_id = {d.id: d for d in self.dataset.documents}
        return build_source_pack(selections, notes_by_id=notes_by_id,
                                 documents_by_id=documents_by_id)

    # -- v2: review metrics (REV-1..REV-3) ---------------------------------------

    def get_review_metrics(self) -> ReviewMetrics:
        """Weak-topic + coach-activity derivation over the coaching log."""
        return compute_review_metrics(self.dataset.coach_sessions)

    # -- derived views --------------------------------------------------------------

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
