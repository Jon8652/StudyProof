"""Bounded provider-neutral replies."""
from dataclasses import dataclass, field


@dataclass
class Quote:
    source_id: str
    page: int
    quote: str
    verified: bool = False


@dataclass
class CoachReply:
    kind: str
    text: str
    quotes: list[Quote] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    sections: dict = field(default_factory=dict)


@dataclass
class AnswerAssessment:
    source_id: str
    topic: str
    question: str
    score: int
    weak_topic_id: str | None = None

