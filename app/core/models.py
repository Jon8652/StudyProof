"""Core domain models: Session, Settings, and the persisted dataset.

No GUI or persistence dependencies — this module is pure data + rules hooks.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from app.constants import (
    AI_MODEL_CHOICES,
    CONFIDENCE_LEVELS,
    DEFAULT_AI_MODEL,
    DEFAULT_THEME,
    DEFAULT_XP_PER_10_MINUTES,
    MAX_INPUT_HOURS,
    MAX_INPUT_MINUTES,
    MAX_SESSION_MINUTES,
    MIN_INPUT_HOURS,
    MIN_INPUT_MINUTES,
    MIN_SESSION_MINUTES,
    SOURCE_CUSTOM,
    SOURCE_PRESET,
    VALID_AI_MODES,
    VALID_THEMES,
)


@dataclass
class Session:
    """A single logged study session (local naive datetimes)."""

    id: str
    start_local: datetime
    end_local: datetime
    duration_minutes: int
    created_at_local: datetime
    source: str = SOURCE_PRESET  # "preset" or "custom"

    # -- construction helpers ------------------------------------------------

    @classmethod
    def create(cls, duration_minutes: int, source: str = SOURCE_PRESET,
               now: datetime | None = None) -> "Session":
        """Create a session ending at ``now`` (FR-6).

        End datetime = current local time; start = end - duration.
        """
        end = now if now is not None else datetime.now()
        start = end - timedelta(minutes=duration_minutes)
        return cls(
            id=str(uuid.uuid4()),
            start_local=start,
            end_local=end,
            duration_minutes=int(duration_minutes),
            created_at_local=end,
            source=source,
        )

    # -- derived properties ---------------------------------------------------

    @property
    def day(self) -> date:
        """Calendar-day attribution: the day of the START datetime (A-3/FR-7)."""
        return self.start_local.date()

    # -- validation ------------------------------------------------------------

    @staticmethod
    def validate_duration(duration_minutes: int) -> str | None:
        """Return a friendly message when invalid, else None (FR-4/A-4)."""
        if not isinstance(duration_minutes, int):
            return "Please enter valid numbers for hours and minutes."
        if duration_minutes < MIN_SESSION_MINUTES:
            return f"Session must be at least {MIN_SESSION_MINUTES} minutes."
        if duration_minutes > MAX_SESSION_MINUTES:
            return "Session cannot be longer than 12 hours."
        return None

    @staticmethod
    def validate_custom_inputs(hours: Any, minutes: Any) -> tuple[int, str | None]:
        """Validate separate hours/minutes inputs (FR-3..FR-5, ERR-1..ERR-4).

        Returns ``(total_minutes, error_message_or_None)``.
        """
        try:
            hours_int = int(str(hours).strip())
            minutes_int = int(str(minutes).strip())
        except (TypeError, ValueError):
            return 0, "Please enter valid numbers for hours and minutes."

        if hours_int < MIN_INPUT_HOURS or hours_int > MAX_INPUT_HOURS:
            return 0, f"Hours must be between {MIN_INPUT_HOURS} and {MAX_INPUT_HOURS}."
        if minutes_int < MIN_INPUT_MINUTES or minutes_int > MAX_INPUT_MINUTES:
            return 0, (
                f"Minutes must be between {MIN_INPUT_MINUTES} and {MAX_INPUT_MINUTES}."
            )

        total = hours_int * 60 + minutes_int
        err = Session.validate_duration(total)
        if err is not None:
            return total, err
        return total, None


MAX_NOTE_TITLE_LENGTH = 120
MAX_NOTE_BODY_LENGTH = 1_000_000


@dataclass
class Note:
    """A titled free-text note, optionally attached to a calendar day.

    Notes are stored alongside sessions in the dataset so they persist with
    the normal autosave/backup flow. ``day`` is None for undated notes.
    """

    id: str
    title: str
    body: str = ""
    day: date | None = None
    created_at_local: datetime = field(default_factory=datetime.now)
    updated_at_local: datetime = field(default_factory=datetime.now)

    @classmethod
    def create(cls, title: str, body: str = "",
               day: date | None = None,
               now: datetime | None = None) -> "Note":
        moment = now if now is not None else datetime.now()
        return cls(
            id=str(uuid.uuid4()),
            title=title.strip(),
            body=body,
            day=day,
            created_at_local=moment,
            updated_at_local=moment,
        )

    @staticmethod
    def sanitize_text(value: Any, limit: int) -> str:
        """Coerce to str and clamp length; never crash on odd payloads."""
        if not isinstance(value, str):
            value = str(value or "")
        return value[:limit]

    def display_title(self) -> str:
        """Title with a friendly fallback when left blank."""
        stripped = self.title.strip()
        return stripped if stripped else "Untitled note"


@dataclass
class AISettings:
    """Study Coach AI configuration persisted inside ``settings.ai`` (AIS-1).

    The API key is local-only (A-28): masked in the UI, never logged.
    """

    mode: str = "mock"                       # "mock" | "live" (A-21 default)
    base_url: str = ""                       # OpenAI-compatible endpoint (A-22)
    model: str = DEFAULT_AI_MODEL            # default deepseek-chat
    api_key: str = field(default="", repr=False)  # local-only secret (A-28)

    @classmethod
    def sanitized(cls, raw: Any) -> "AISettings":
        """Coerce a raw dict to safe values; never reject the file (AIS-5)."""
        raw = raw if isinstance(raw, dict) else {}
        mode = raw.get("mode", "mock")
        if mode not in VALID_AI_MODES:
            mode = "mock"
        base_url = raw.get("base_url", "")
        if not isinstance(base_url, str):
            base_url = str(base_url or "")
        base_url = base_url.strip()
        model = raw.get("model", DEFAULT_AI_MODEL)
        if not isinstance(model, str):
            model = str(model or "")
        model = model.strip()
        # Change request #4: the model must be one of the exact names in
        # AI_MODEL_CHOICES.  Anything else (free text from older save
        # files, typos, empty strings) is coerced to the default choice.
        if model not in AI_MODEL_CHOICES:
            model = DEFAULT_AI_MODEL
        api_key = raw.get("api_key", "")
        if not isinstance(api_key, str):
            api_key = str(api_key or "")
        return cls(mode=mode, base_url=base_url, model=model,
                   api_key=api_key.strip())


@dataclass
class Settings:
    """User settings persisted in the save file (FR-73 / DATA-12)."""

    theme: str = DEFAULT_THEME
    xp_per_10_minutes: int = DEFAULT_XP_PER_10_MINUTES
    ai: AISettings = field(default_factory=AISettings)

    @classmethod
    def sanitized(cls, raw: dict[str, Any] | None) -> "Settings":
        """Build settings with safe defaults for missing/invalid values (FR-74)."""
        raw = raw if isinstance(raw, dict) else {}
        theme = raw.get("theme", DEFAULT_THEME)
        if theme not in VALID_THEMES:
            theme = DEFAULT_THEME
        xp_rate = raw.get("xp_per_10_minutes", DEFAULT_XP_PER_10_MINUTES)
        try:
            xp_rate = int(xp_rate)
        except (TypeError, ValueError):
            xp_rate = DEFAULT_XP_PER_10_MINUTES
        if xp_rate < 0:
            xp_rate = DEFAULT_XP_PER_10_MINUTES
        ai = AISettings.sanitized(raw.get("ai"))
        return cls(theme=theme, xp_per_10_minutes=xp_rate, ai=ai)


MAX_PAGE_TEXT_CHARS = 200_000   # §14.1 defensive per-page clamp


@dataclass
class Document:
    """A PDF library record: metadata + extracted per-page text (§14).

    The binary itself lives under ``<app-data>/library/<stored_name>``
    (A-29/DATA-10); the record survives independently of the original path.
    ``status`` is one of ready / textless / missing / extracting.
    """

    id: str
    file_name: str
    stored_name: str                         # "<uuid>.pdf" inside library/
    original_path: str = ""
    imported_at_local: datetime = field(default_factory=datetime.now)
    page_count: int = 0
    char_count: int = 0
    status: str = "extracting"
    text_pages: list[dict[str, Any]] = field(default_factory=list)  # {page,text}

    @classmethod
    def create(cls, file_name: str, stored_name: str, original_path: str = "",
               now: datetime | None = None) -> "Document":
        moment = now if now is not None else datetime.now()
        return cls(
            id=str(uuid.uuid4()),
            file_name=file_name,
            stored_name=stored_name,
            original_path=original_path,
            imported_at_local=moment,
            status="extracting",
        )

    def set_extracted_text(self, pages: list[tuple[int, str]]) -> None:
        """Store extraction results (1-based page numbers, verbatim text).

        Individual oversized pages are clamped defensively (§14.1).
        Zero-text documents become ``textless`` (PDF-6/ERR-31); otherwise the
        document is ``ready`` for selection (PDF-4).
        """
        cleaned: list[dict[str, Any]] = []
        total = 0
        for page_no, text in pages:
            if not isinstance(text, str):
                text = str(text or "")
            text = text[:MAX_PAGE_TEXT_CHARS]
            cleaned.append({"page": int(page_no), "text": text})
            total += len(text)
        self.text_pages = cleaned
        self.page_count = len(cleaned)
        self.char_count = total
        self.status = "ready" if total > 0 else "textless"

    def display_label(self) -> str:
        name = (self.file_name or "").strip()
        return name if name else "Untitled document"


SOURCE_KIND_NOTE = "note"
SOURCE_KIND_DOCUMENT = "document"


@dataclass
class SourceRef:
    """Selection snapshot entry (SEL-3): survives source deletion (ERR-33)."""

    kind: str            # "note" | "document"
    id: str
    label: str

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "id": self.id, "label": self.label}


@dataclass
class QuestionRecord:
    """One coached question inside a session (§14, COACH-*).

    State machine (COACH-1): open → answered → confidence_set →
    feedback_given → (revised | followup)* → closed — represented by the
    combination of status + first_answer/confidence/feedback_given flags.
    """

    id: str
    prompt: str = ""
    parent_id: str | None = None
    spans: list[dict[str, Any]] = field(default_factory=list)  # {source_id,page,text}
    status: str = "open"                     # open | answered | closed
    first_answer: str | None = None          # immutable once set (A-25)
    first_answer_at: datetime | None = None
    confidence: str | None = None            # low|medium|high|null (A-36)
    confidence_at: datetime | None = None
    revisions: list[dict[str, Any]] = field(default_factory=list)  # {text,at}
    hints_seen: int = 0
    feedback_given: bool = False
    followup_ids: list[str] = field(default_factory=list)
    answer_key: dict[str, Any] = field(default_factory=dict)  # mock cloze key

    @classmethod
    def create(cls, prompt: str, spans: list[dict[str, Any]] | None = None,
               parent_id: str | None = None,
               answer_key: dict[str, Any] | None = None) -> "QuestionRecord":
        return cls(id=str(uuid.uuid4()), prompt=prompt,
                   spans=list(spans or []), parent_id=parent_id,
                   answer_key=dict(answer_key or {}))


@dataclass
class CoachTurn:
    """One persisted conversation entry (coach or user) — §14 turns[]."""

    id: str
    at_local: datetime = field(default_factory=datetime.now)
    role: str = "coach"                      # coach | user
    kind: str = "notice"                     # question|answer|confidence|hint|
                                             # feedback|revision|followup|notice
    question_id: str | None = None
    mode: str | None = None                  # effective mode per turn (AIS-2)
    text: str = ""
    confidence: str | None = None
    quotes: list[dict[str, Any]] = field(default_factory=list)  # +verified flag
    ungrounded_count: int = 0

    @classmethod
    def create(cls, role: str, kind: str, text: str,
               question_id: str | None = None, mode: str | None = None,
               confidence: str | None = None,
               quotes: list[dict[str, Any]] | None = None,
               ungrounded_count: int = 0,
               at: datetime | None = None) -> "CoachTurn":
        return cls(
            id=str(uuid.uuid4()),
            at_local=at if at is not None else datetime.now(),
            role=role, kind=kind, text=text, question_id=question_id,
            mode=mode, confidence=confidence,
            quotes=list(quotes or []), ungrounded_count=ungrounded_count,
        )


TURN_KINDS = ("question", "answer", "confidence", "hint", "feedback",
              "revision", "followup", "notice")


@dataclass
class CoachSession:
    """A persisted coaching conversation container (§14 coach_sessions[])."""

    id: str
    created_at_local: datetime = field(default_factory=datetime.now)
    updated_at_local: datetime = field(default_factory=datetime.now)
    sources: list[SourceRef] = field(default_factory=list)
    questions: list[QuestionRecord] = field(default_factory=list)
    turns: list[CoachTurn] = field(default_factory=list)

    @classmethod
    def create(cls, sources: list[SourceRef],
               now: datetime | None = None) -> "CoachSession":
        moment = now if now is not None else datetime.now()
        return cls(id=str(uuid.uuid4()), created_at_local=moment,
                   updated_at_local=moment, sources=list(sources))

    def question_by_id(self, question_id: str) -> QuestionRecord | None:
        for question in self.questions:
            if question.id == question_id:
                return question
        return None

    def touch(self, now: datetime | None = None) -> None:
        self.updated_at_local = now if now is not None else datetime.now()


@dataclass
class Dataset:
    """The full in-memory dataset: settings + all collections (single source of truth)."""

    settings: Settings = field(default_factory=Settings)
    sessions: list[Session] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)
    documents: list[Document] = field(default_factory=list)
    coach_sessions: list[CoachSession] = field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def empty(cls, now: datetime | None = None) -> "Dataset":
        moment = now if now is not None else datetime.now()
        return cls(settings=Settings(), sessions=[], notes=[], documents=[],
                   coach_sessions=[], created_at=moment, updated_at=moment)

    def session_by_id(self, session_id: str) -> Session | None:
        """Look up a session by id (O(n) scan; AppState keeps an id map)."""
        for session in self.sessions:
            if session.id == session_id:
                return session
        return None

    def note_by_id(self, note_id: str) -> Note | None:
        """Look up a note by id (O(n) scan; AppState keeps an id map)."""
        for note in self.notes:
            if note.id == note_id:
                return note
        return None

    def document_by_id(self, document_id: str) -> Document | None:
        for document in self.documents:
            if document.id == document_id:
                return document
        return None

    def coach_session_by_id(self, coach_id: str) -> CoachSession | None:
        for coach in self.coach_sessions:
            if coach.id == coach_id:
                return coach
        return None
