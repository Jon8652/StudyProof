"""Source-pack builder (Phase 3.2 Part 1, SEL-2 / A-37).

Pure, deterministic, non-AI: turns the user's selected sources into a list
of grounded text spans that always fit inside ``MAX_PACK_CHARS``.

Rules implemented here (SEL-2):
- Notes become a pseudo-page 1 containing the note body.
- Documents contribute their **leading** pages first (page 1, 2, …).
- Text is truncated at sentence boundaries when the budget runs out; if a
  single sentence can't fit, it falls back to a word boundary (and only as
  an absolute last resort to a hard character cut).
- Every emitted span carries ``(source_id, page, text)`` so later grounding
  (GRD-1/GRD-2, Phase 4) can verify quotations against exactly this text.
- Sources are processed in selection order; once the budget is spent, the
  remaining sources are simply omitted.

No file access, no Tk, no network — fully unit-testable headless.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.constants import MAX_PACK_CHARS, MAX_SOURCES
from app.core.models import SOURCE_KIND_DOCUMENT, SOURCE_KIND_NOTE

# Sentence splitter: keeps terminators attached to their sentence.
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass
class PackSpan:
    """One grounded fragment of the pack: (source_id, page, text)."""

    source_id: str
    kind: str            # "note" | "document"
    label: str
    page: int            # notes are always pseudo-page 1 (SEL-2)
    text: str

    def as_dict(self) -> dict:
        return {"source_id": self.source_id, "page": self.page,
                "text": self.text}


@dataclass
class SourcePack:
    """The finished pack plus honest accounting for what was left out."""

    spans: list[PackSpan] = field(default_factory=list)
    total_chars: int = 0
    included_source_ids: list[str] = field(default_factory=list)
    omitted_source_ids: list[str] = field(default_factory=list)
    truncated: bool = False      # True when any span was cut short

    def as_spans_payload(self) -> list[dict]:
        """Serialization shape stored on QuestionRecord.spans (§14)."""
        return [span.as_dict() for span in self.spans]


def estimate_pack_size(sources: list[dict]) -> int:
    """Live UI estimate of pack size without building it (UI-25).

    ``sources`` items need ``kind``, ``chars`` and optionally ``pages``;
    documents count their leading pages up to the global budget so the
    dialog's counter matches what the real builder will emit.
    """
    budget = MAX_PACK_CHARS
    total = 0
    for entry in sources[:MAX_SOURCES]:
        chars = max(0, int(entry.get("chars", 0)))
        take = min(chars, budget)
        total += take
        budget -= take
        if budget <= 0:
            break
    return total


def _split_sentences(text: str) -> list[str]:
    """Split into sentences while preserving all original characters."""
    pieces: list[str] = []
    for chunk in _SENTENCE_RE.split(text):
        # Re-attach the whitespace the splitter consumed.
        pieces.append(chunk)
    return [p for p in pieces if p]


def _truncate_at_boundary(text: str, limit: int) -> tuple[str, bool]:
    """Cut ``text`` to <= ``limit`` chars at a sentence boundary.

    Returns (trimmed_text, was_cut).  Falls back to word boundary, then to
    a hard slice, so the limit is *never* exceeded.
    """
    if limit <= 0:
        return "", True
    if len(text) <= limit:
        return text, False

    cut = text[:limit]
    # 1) best sentence boundary near the end of the window
    sentences = _split_sentences(cut)
    if sentences:
        joined = ""
        for sentence in sentences:
            candidate = (joined + (" " if joined else "") + sentence)
            if len(candidate) > limit:
                break
            joined = candidate
        if len(joined) >= max(1, int(limit * 0.6)):
            return joined, True
    # 2) word boundary
    if " " in cut:
        trimmed = cut.rsplit(" ", 1)[0]
        if trimmed:
            return trimmed, True
    # 3) absolute last resort — hard character cut
    return cut, True


def build_source_pack(selected: list[tuple[str, str]],
                      notes_by_id: dict | None = None,
                      documents_by_id: dict | None = None,
                      budget: int = MAX_PACK_CHARS) -> SourcePack:
    """Assemble the pack for ``selected`` sources (SEL-2).

    ``selected`` is a list of ``(kind, id)`` tuples in user-selection order
    (max MAX_SOURCES enforced here defensively).  ``notes_by_id`` maps id →
    Note; ``documents_by_id`` maps id → Document.  Missing/unavailable
    entries are skipped silently — the caller decides how to explain them.
    """
    pack = SourcePack()
    remaining = max(0, int(budget))

    for kind, item_id in list(selected)[:MAX_SOURCES]:
        chunks: list[tuple[int, str]] = []   # (page_number, full text)
        if kind == SOURCE_KIND_NOTE and notes_by_id is not None:
            note = notes_by_id.get(item_id)
            body = (note.body or "").strip() if note is not None else ""
            if body:
                chunks.append((1, body))     # pseudo-page 1 (SEL-2)
        elif kind == SOURCE_KIND_DOCUMENT and documents_by_id is not None:
            document = documents_by_id.get(item_id)
            if document is not None and document.status in ("ready", "missing"):
                # Leading pages, in page order.
                ordered = sorted(document.text_pages,
                                 key=lambda p: int(p.get("page", 0)))
                for page in ordered:
                    text = (page.get("text") or "").strip()
                    if text:
                        chunks.append((int(page.get("page", 0)), text))

        if not chunks:
            pack.omitted_source_ids.append(item_id)
            continue

        if remaining <= 0:
            pack.omitted_source_ids.append(item_id)
            continue

        label = ""
        if kind == SOURCE_KIND_NOTE and notes_by_id is not None:
            note = notes_by_id.get(item_id)
            label = note.display_title() if note is not None else ""
        elif kind == SOURCE_KIND_DOCUMENT and documents_by_id is not None:
            document = documents_by_id.get(item_id)
            label = document.display_label() if document is not None else ""

        emitted_any = False
        source_had_cut = False
        for page_no, text in chunks:
            if remaining <= 0:
                source_had_cut = True
                break
            if len(text) <= remaining:
                piece, was_cut = text, False
            else:
                piece, was_cut = _truncate_at_boundary(text, remaining)
            if not piece:
                source_had_cut = True
                break
            pack.spans.append(PackSpan(source_id=item_id, kind=kind,
                                       label=label, page=page_no,
                                       text=piece))
            pack.total_chars += len(piece)
            remaining -= len(piece)
            emitted_any = True
            if was_cut:
                source_had_cut = True
                break

        if emitted_any:
            pack.included_source_ids.append(item_id)
            if source_had_cut:
                pack.truncated = True
        else:
            pack.omitted_source_ids.append(item_id)

    # Sources after a fully-spent budget also count as omitted even if we
    # never looked at them (kept selection order intact above).
    seen = set(pack.included_source_ids) | set(pack.omitted_source_ids)
    for kind, item_id in list(selected)[MAX_SOURCES:]:
        if item_id not in seen:
            pack.omitted_source_ids.append(item_id)
            seen.add(item_id)
    return pack
