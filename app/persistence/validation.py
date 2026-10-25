"""Load-time validation for save-file payloads (Part C).

Turns a raw JSON dictionary into a validated ``Dataset`` plus a list of
friendly warnings. Invalid session records are skipped where practical
(ERR-10); structurally broken files raise CorruptFileError; newer schema
versions raise FutureSchemaError (FR-72).
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.constants import SCHEMA_VERSION, SOURCE_CUSTOM, SOURCE_PRESET, SOURCE_TIMER
from app.core.models import (
    CONFIDENCE_LEVELS,
    CoachSession,
    CoachTurn,
    Dataset,
    Document,
    MAX_NOTE_BODY_LENGTH,
    MAX_NOTE_TITLE_LENGTH,
    Note,
    QuestionRecord,
    Session,
    Settings,
    SourceRef,
    TURN_KINDS,
    WeakTopic,
)
from app.persistence.schema import CorruptFileError, FutureSchemaError
from app.utils.date_utils import parse_dt

VALID_DOC_STATUSES = ("ready", "textless", "missing")
VALID_QUESTION_STATUSES = ("open", "answered", "closed")
VALID_TURN_ROLES = ("coach", "user")
VALID_SOURCE_KINDS = ("note", "document")


def validate_payload(payload: Any) -> tuple[Dataset, list[str]]:
    """Validate a parsed JSON payload and return (dataset, warnings).

    Raises:
        CorruptFileError: the payload is not a usable v1 save structure.
        FutureSchemaError: the payload declares a newer schema version.
    """
    warnings: list[str] = []

    if not isinstance(payload, dict):
        raise CorruptFileError("Save file is not a valid data file.")

    # --- schema version (FR-70 / FR-71) --------------------------------------
    raw_version = payload.get("schema_version")
    if raw_version is None:
        version = 1  # missing version treated as v1 when otherwise valid
    else:
        try:
            version = int(raw_version)
        except (TypeError, ValueError):
            raise CorruptFileError("Save file has an unreadable version.")
        if version > SCHEMA_VERSION:
            raise FutureSchemaError(
                f"Save file uses version {version}; this app supports "
                f"version {SCHEMA_VERSION}."
            )
        if version < 1:
            version = 1

    # --- settings (FR-73 / FR-74 safe defaults) -------------------------------
    settings = Settings.sanitized(payload.get("settings"))

    # --- sessions -------------------------------------------------------------
    raw_sessions = payload.get("sessions")
    if raw_sessions is None:
        raw_sessions = []
    if not isinstance(raw_sessions, list):
        raise CorruptFileError("Save file's session list is damaged.")

    sessions: list[Session] = []
    skipped = 0
    seen_ids: set[str] = set()
    for record in raw_sessions:
        session = _parse_session(record)
        if session is None:
            skipped += 1
            continue
        if session.id in seen_ids:  # DATA-6 unique ids — skip duplicates
            skipped += 1
            continue
        seen_ids.add(session.id)
        sessions.append(session)

    if skipped:
        from app.persistence.schema import FRIENDLY_MESSAGES

        warnings.append(FRIENDLY_MESSAGES["partial_sessions"])

    # --- notes (optional section; missing/invalid entries are skipped) --------
    raw_notes = payload.get("notes")
    if raw_notes is None:
        raw_notes = []
    if not isinstance(raw_notes, list):
        raise CorruptFileError("Save file's note list is damaged.")

    notes: list[Note] = []
    notes_skipped = 0
    seen_note_ids: set[str] = set()
    for record in raw_notes:
        note = _parse_note(record)
        if note is None:
            notes_skipped += 1
            continue
        if note.id in seen_note_ids:
            notes_skipped += 1
            continue
        seen_note_ids.add(note.id)
        notes.append(note)

    if notes_skipped:
        from app.persistence.schema import FRIENDLY_MESSAGES

        if FRIENDLY_MESSAGES["partial_sessions"] not in warnings:
            warnings.append(FRIENDLY_MESSAGES["partial_sessions"])

    # --- documents (schema v2, optional; DATA-8/DATA-9) -----------------------
    raw_documents = payload.get("documents")
    if raw_documents is None:
        raw_documents = []          # v1 payload → empty list (DATA-7 migration)
    if not isinstance(raw_documents, list):
        raise CorruptFileError("Save file's document list is damaged.")

    documents: list[Document] = []
    docs_skipped = 0
    seen_doc_ids: set[str] = set()
    for record in raw_documents:
        document = _parse_document(record)
        if document is None:
            docs_skipped += 1
            continue
        if document.id in seen_doc_ids:      # DATA-9 duplicate ids skipped
            docs_skipped += 1
            continue
        seen_doc_ids.add(document.id)
        documents.append(document)

    # --- coach sessions (schema v2, optional; DATA-8/DATA-9) ------------------
    raw_coach = payload.get("coach_sessions")
    if raw_coach is None:
        raw_coach = []              # v1 payload → empty list (DATA-7 migration)
    if not isinstance(raw_coach, list):
        raise CorruptFileError("Save file's coach session list is damaged.")

    coach_sessions: list[CoachSession] = []
    coach_skipped = 0
    seen_coach_ids: set[str] = set()
    for record in raw_coach:
        coach = _parse_coach_session(record)
        if coach is None:
            coach_skipped += 1
            continue
        if coach.id in seen_coach_ids:       # DATA-9 duplicate ids skipped
            coach_skipped += 1
            continue
        seen_coach_ids.add(coach.id)
        coach_sessions.append(coach)

    if docs_skipped or coach_skipped:
        from app.persistence.schema import FRIENDLY_MESSAGES

        if FRIENDLY_MESSAGES["partial_sessions"] not in warnings:
            warnings.append(FRIENDLY_MESSAGES["partial_sessions"])

    created_at = _parse_optional_dt(payload.get("created_at"))
    updated_at = _parse_optional_dt(payload.get("updated_at"))

    dataset = Dataset(
        settings=settings,
        sessions=sessions,
        notes=notes,
        documents=documents,
        coach_sessions=coach_sessions,
        weak_topics=_parse_weak_topics(payload.get("weak_topics", []), warnings),
        created_at=created_at or datetime.now(),
        updated_at=updated_at,
    )
    return dataset, warnings


def _parse_weak_topics(records, warnings):
    if not isinstance(records, list):
        warnings.append('The weak topic list was damaged and could not be loaded.')
        return []
    topics, seen = [], set()
    for record in records:
        if not isinstance(record, dict):
            continue
        source = _parse_source_ref(record.get('source'))
        required = ('id', 'topic', 'question', 'session_id', 'question_id')
        if (not source or any(not isinstance(record.get(k), str) or not record[k].strip()
                              for k in required)
                or type(record.get('score')) is not int or not 0 <= record['score'] <= 100
                or record['id'] in seen):
            continue
        seen.add(record['id'])
        topics.append(WeakTopic(
            record['id'], source, record['topic'][:160], record['question'][:4000],
            record['session_id'], record['question_id'], record['score'],
            _parse_optional_dt(record.get('updated_at')) or datetime.now()))
    return topics


def _parse_optional_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    return parse_dt(value)


def _parse_session(record: Any) -> Session | None:
    """Parse one session record; return None when unusable (ERR-10)."""
    if not isinstance(record, dict):
        return None

    sid = record.get("id")
    if not isinstance(sid, str) or not sid.strip():
        return None

    start = parse_dt(record.get("start_local", ""))
    if start is None:
        return None

    try:
        duration = int(record.get("duration_minutes"))
    except (TypeError, ValueError):
        return None
    if duration <= 0 or duration > 24 * 60:
        # Reject nonsensical durations rather than crashing.
        return None

    end = parse_dt(record.get("end_local", "")) if record.get("end_local") else None
    if end is None:
        # end_local optional: derive from start + duration (DATA schema 10.4)
        from datetime import timedelta

        end = start + timedelta(minutes=duration)

    created = parse_dt(record.get("created_at_local", "")) if record.get("created_at_local") else None
    if created is None:
        created = end

    source = record.get("source")
    if source not in (SOURCE_PRESET, SOURCE_CUSTOM, SOURCE_TIMER):
        source = SOURCE_PRESET

    try:
        return Session(
            id=sid.strip(),
            start_local=start,
            end_local=end,
            duration_minutes=duration,
            created_at_local=created,
            source=source,
        )
    except Exception:
        return None


def _parse_note(record: Any) -> Note | None:
    """Parse one note record; return None when unusable (ERR-10 style)."""
    if not isinstance(record, dict):
        return None

    nid = record.get("id")
    if not isinstance(nid, str) or not nid.strip():
        return None

    title = Note.sanitize_text(record.get("title", ""), MAX_NOTE_TITLE_LENGTH)
    body = Note.sanitize_text(record.get("body", ""), MAX_NOTE_BODY_LENGTH)

    day: date | None = None
    raw_day = record.get("day")
    if raw_day:
        # Notes store a plain ISO date ("YYYY-MM-DD"); accept datetime too.
        parsed_dt = parse_dt(raw_day)
        if parsed_dt is not None:
            day = parsed_dt.date()

    created = _parse_optional_dt(record.get("created_at_local"))
    updated = _parse_optional_dt(record.get("updated_at_local"))
    now = datetime.now()
    if created is None:
        created = updated or now
    if updated is None:
        updated = created

    try:
        return Note(
            id=nid.strip(),
            title=title,
            body=body,
            day=day,
            created_at_local=created,
            updated_at_local=updated,
        )
    except Exception:
        return None


# --- schema v2 record parsers (DATA-9: invalid records skipped, ERR-21 style) --

def _coerce_str(value: Any, limit: int = 0) -> str:
    if not isinstance(value, str):
        value = str(value or "")
    return value[:limit] if limit else value


def _parse_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_document(record: Any) -> Document | None:
    """Parse one document record; return None when unusable (DATA-9)."""
    if not isinstance(record, dict):
        return None

    doc_id = record.get("id")
    if not isinstance(doc_id, str) or not doc_id.strip():
        return None
    stored_name = record.get("stored_name")
    if not isinstance(stored_name, str) or not stored_name.strip():
        return None

    from app.core.models import MAX_PAGE_TEXT_CHARS

    raw_pages = record.get("text_pages")
    if raw_pages is None:
        raw_pages = []
    if not isinstance(raw_pages, list):
        return None
    text_pages: list[dict[str, Any]] = []
    for entry in raw_pages:
        if not isinstance(entry, dict):
            continue
        page_no = _parse_int(entry.get("page"), default=0)
        if page_no < 1:
            continue
        text = _coerce_str(entry.get("text", ""), MAX_PAGE_TEXT_CHARS)
        text_pages.append({"page": page_no, "text": text})

    status = record.get("status")
    if status not in VALID_DOC_STATUSES:
        # §14.1: unknown → missing (safest: viewer disabled, text kept).
        status = "missing"

    imported = _parse_optional_dt(record.get("imported_at_local")) \
        or datetime.now()

    try:
        return Document(
            id=doc_id.strip(),
            file_name=_coerce_str(record.get("file_name", ""), 512),
            stored_name=Path(str(stored_name).strip()).name,
            original_path=_coerce_str(record.get("original_path", ""), 1024),
            imported_at_local=imported,
            page_count=max(0, _parse_int(record.get("page_count"))),
            char_count=max(0, _parse_int(record.get("char_count"))),
            status=status,
            text_pages=text_pages,
        )
    except Exception:
        return None


def _parse_source_ref(entry: Any) -> SourceRef | None:
    if not isinstance(entry, dict):
        return None
    kind = entry.get("kind")
    sid = entry.get("id")
    if kind not in VALID_SOURCE_KINDS:
        return None
    if not isinstance(sid, str) or not sid.strip():
        return None
    label = _coerce_str(entry.get("label", ""), 256)
    return SourceRef(kind=kind, id=sid.strip(), label=label)


def _parse_question(record: Any) -> QuestionRecord | None:
    """Parse one question record; return None when unusable (DATA-9)."""
    if not isinstance(record, dict):
        return None
    qid = record.get("id")
    if not isinstance(qid, str) or not qid.strip():
        return None

    raw_spans = record.get("spans")
    spans: list[dict[str, Any]] = []
    if isinstance(raw_spans, list):
        for span in raw_spans:
            if not isinstance(span, dict):
                continue
            source_id = span.get("source_id")
            if not isinstance(source_id, str) or not source_id.strip():
                continue
            spans.append({
                "source_id": source_id.strip(),
                "page": max(0, _parse_int(span.get("page"))),
                "text": _coerce_str(span.get("text", "")),
            })

    raw_revisions = record.get("revisions")
    revisions: list[dict[str, Any]] = []
    if isinstance(raw_revisions, list):
        for revision in raw_revisions:
            if not isinstance(revision, dict):
                continue
            moment = _parse_optional_dt(revision.get("at")) or datetime.now()
            revisions.append({"text": _coerce_str(revision.get("text", "")),
                              "at": moment})

    status = record.get("status")
    if status not in VALID_QUESTION_STATUSES:
        status = "open"

    confidence = record.get("confidence")
    if confidence not in CONFIDENCE_LEVELS:
        confidence = None

    parent_id = record.get("parent_id")
    if not isinstance(parent_id, str) or not parent_id.strip():
        parent_id = None

    followup_ids = [f for f in (record.get("followup_ids") or [])
                    if isinstance(f, str) and f.strip()] \
        if isinstance(record.get("followup_ids"), list) else []


    first_answer = record.get("first_answer")
    if first_answer is not None and not isinstance(first_answer, str):
        first_answer = str(first_answer)

    try:
        return QuestionRecord(
            id=qid.strip(),
            prompt=_coerce_str(record.get("prompt", "")),
            parent_id=parent_id,
            spans=spans,
            status=status,
            first_answer=first_answer,
            first_answer_at=_parse_optional_dt(record.get("first_answer_at")),
            confidence=confidence,
            confidence_at=_parse_optional_dt(record.get("confidence_at")),
            revisions=revisions,
            hints_seen=max(0, _parse_int(record.get("hints_seen"))),
            feedback_given=bool(record.get("feedback_given", False)),
            followup_ids=followup_ids,
        )
    except Exception:
        return None


def _parse_turn(record: Any) -> CoachTurn | None:
    """Parse one coach turn; return None when unusable (DATA-9)."""
    if not isinstance(record, dict):
        return None
    tid = record.get("id")
    if not isinstance(tid, str) or not tid.strip():
        return None

    role = record.get("role")
    if role not in VALID_TURN_ROLES:
        return None
    kind = record.get("kind")
    if kind not in TURN_KINDS:
        return None

    mode = record.get("mode")
    if mode != "live":
        mode = None

    confidence = record.get("confidence")
    if confidence not in CONFIDENCE_LEVELS:
        confidence = None

    question_id = record.get("question_id")
    if not isinstance(question_id, str) or not question_id.strip():
        question_id = None

    raw_quotes = record.get("quotes")
    quotes: list[dict[str, Any]] = []
    if isinstance(raw_quotes, list):
        for quote in raw_quotes:
            if not isinstance(quote, dict):
                continue
            source_id = quote.get("source_id")
            if not isinstance(source_id, str) or not source_id.strip():
                continue
            quotes.append({
                "source_id": source_id.strip(),
                "page": max(0, _parse_int(quote.get("page"))),
                "quote": _coerce_str(quote.get("quote", "")),
                "verified": bool(quote.get("verified", False)),
            })

    try:
        return CoachTurn(
            id=tid.strip(),
            at_local=_parse_optional_dt(record.get("at_local"))
            or datetime.now(),
            role=role,
            kind=kind,
            question_id=question_id,
            mode=mode,
            text=_coerce_str(record.get("text", "")),
            confidence=confidence,
            quotes=quotes,
            ungrounded_count=max(0, _parse_int(record.get("ungrounded_count"))),
            recap=_parse_recap(record.get("recap")) if kind == 'recap' else None,
            assessment=_parse_assessment(record.get("assessment")),
            answer_intent=record.get('answer_intent') if type(record.get('answer_intent')) is bool else None,
        )
    except Exception:
        return None


def _parse_recap(value):
    if not isinstance(value, dict) or value.get('days') not in (7, 30):
        return None
    links = []
    for item in value.get('loose_ends', [])[:5] if isinstance(value.get('loose_ends'), list) else []:
        if isinstance(item, dict) and all(isinstance(item.get(k), str) for k in
                                         ('session_id', 'question_id', 'question', 'label', 'asked')):
            link = {k: item[k][:240] for k in
                    ('session_id', 'question_id', 'question', 'label', 'asked')}
            for key in ('title', 'turn_id'):
                if isinstance(item.get(key), str):
                    link[key] = item[key][:240]
            links.append(link)
    return {'days': value['days'], 'loose_ends': links,
            'offer': value['days'] == 7 and value.get('offer') is True,
            'revise': value.get('revise') is True,
            'simplified': value.get('simplified') is True}


def _parse_assessment(value):
    if (not isinstance(value, dict) or type(value.get('score')) is not int
            or not 0 <= value['score'] <= 100
            or not all(isinstance(value.get(k), str) and value[k].strip()
                       for k in ('source_id', 'topic', 'question'))):
        return None
    result = {k: value[k] for k in ('source_id', 'topic', 'question', 'score')}
    previous_at = _parse_optional_dt(value.get('previous_at'))
    if previous_at and type(value.get('previous_score')) is int and 0 <= value['previous_score'] <= 100:
        result.update(previous_score=value['previous_score'], previous_at=previous_at.isoformat())
    return result


def _parse_coach_session(record: Any) -> CoachSession | None:
    """Parse one coach session; skip invalid sub-records (DATA-9)."""
    if not isinstance(record, dict):
        return None
    cid = record.get("id")
    if not isinstance(cid, str) or not cid.strip():
        return None

    raw_sources = record.get("sources")
    if not isinstance(raw_sources, list):
        return None                      # sources[] is required (§14.1)
    sources = [ref for ref in map(_parse_source_ref, raw_sources)
               if ref is not None]

    raw_questions = record.get("questions")
    if raw_questions is None:
        raw_questions = []
    if not isinstance(raw_questions, list):
        return None
    questions: list[QuestionRecord] = []
    seen_qids: set[str] = set()
    for entry in raw_questions:
        question = _parse_question(entry)
        if question is None or question.id in seen_qids:
            continue
        seen_qids.add(question.id)
        questions.append(question)

    raw_turns = record.get("turns")
    if raw_turns is None:
        raw_turns = []
    if not isinstance(raw_turns, list):
        return None
    turns: list[CoachTurn] = []
    seen_tids: set[str] = set()
    for entry in raw_turns:
        turn = _parse_turn(entry)
        if turn is None or turn.id in seen_tids:
            continue
        seen_tids.add(turn.id)
        turns.append(turn)

    created = _parse_optional_dt(record.get("created_at_local")) \
        or datetime.now()
    updated = _parse_optional_dt(record.get("updated_at_local")) or created

    try:
        return CoachSession(
            id=cid.strip(),
            created_at_local=created,
            updated_at_local=updated,
            sources=sources,
            questions=questions,
            turns=turns,
        )
    except Exception:
        return None
