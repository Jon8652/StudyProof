"""Statistics engine (Part B) + v2 review metrics (§9.4).

Computes every required statistic from the session list alone:
- Today / week / month / lifetime XP and time (FR-38..FR-42)
- Current streak / longest streak (FR-44)
- Total sessions, active days, average daily study time (FR-45, FR-50)
- Bronze / Silver / Gold day counts using highest-medal-per-day only
  (A-11 / FR-46)

v2 additions (pure derivation over the coaching log — no AI needed):
- ``compute_review_metrics``: weak-topic scores per source (REV-1), coach
  activity totals (REV-3) — used by the Stats "Review" section (UI-24).

All period math uses local dates and the same start-day attribution rule.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Iterable

from app.constants import STREAK_QUALIFYING_MIN_MINUTES
from app.core.medals import BRONZE, DayAggregate, GOLD, SILVER, \
    build_day_aggregates
from app.core.models import CoachSession, Note, Session, Settings
from app.core.streaks import current_streak, longest_streak
from app.utils.date_utils import month_bounds, week_bounds


@dataclass
class PeriodTotals:
    minutes: int = 0
    xp: int = 0


@dataclass
class StatsResult:
    today: PeriodTotals = field(default_factory=PeriodTotals)
    week: PeriodTotals = field(default_factory=PeriodTotals)
    month: PeriodTotals = field(default_factory=PeriodTotals)
    lifetime: PeriodTotals = field(default_factory=PeriodTotals)

    current_streak_days: int = 0
    longest_streak_days: int = 0

    total_sessions: int = 0
    active_days: int = 0
    average_daily_minutes: float = 0.0

    bronze_count: int = 0
    silver_count: int = 0
    gold_count: int = 0

    total_notes: int = 0


def _sum_period(day_map: dict[date, tuple[int, int]],
                start: date, end: date) -> PeriodTotals:
    minutes = xp = 0
    for day, (m, x) in day_map.items():
        if start <= day <= end:
            minutes += m
            xp += x
    return PeriodTotals(minutes=minutes, xp=xp)


def _count_medals(aggregates: Iterable[DayAggregate]) -> tuple[int, int, int]:
    """Return (bronze, silver, gold) day counts — highest medal per day only."""
    bronze = silver = gold = 0
    for agg in aggregates:
        if agg.medal == GOLD:
            gold += 1
        elif agg.medal == SILVER:
            silver += 1
        elif agg.medal == BRONZE:
            bronze += 1
    return bronze, silver, gold


def compute_stats(
    sessions: Iterable[Session],
    settings: Settings | None = None,
    today: date | None = None,
    day_index: dict[date, DayAggregate] | None = None,
    notes: Iterable[Note] | None = None,
) -> StatsResult:
    """Compute all statistics for the given sessions.

    ``day_index`` (Part L): an already-maintained daily aggregate index may
    be supplied to skip rebuilding aggregates from the full session list.
    ``notes``: optional note collection; when provided, its size is
    reported as the total-notes statistic.
    """
    sessions = list(sessions)
    xp_rate = settings.xp_per_10_minutes if settings else 10
    if today is None:
        today = date.today()

    aggregates = day_index if day_index is not None \
        else build_day_aggregates(sessions, xp_rate)

    # Fast per-day lookup of (minutes, xp)
    day_map: dict[date, tuple[int, int]] = {
        day: (agg.total_minutes, agg.total_xp)
        for day, agg in aggregates.items()
    }

    result = StatsResult()
    result.today = _sum_period(day_map, today, today)

    week_start, week_end = week_bounds(today)
    result.week = _sum_period(day_map, week_start, week_end)

    month_start, month_end = month_bounds(today)
    result.month = _sum_period(day_map, month_start, month_end)

    total_minutes = sum(m for m, _ in day_map.values())
    total_xp = sum(x for _, x in day_map.values())
    result.lifetime = PeriodTotals(minutes=total_minutes, xp=total_xp)

    # Active day = at least one logged session attributed to that day (FR-50).
    # Streaks and active-day count derive from the aggregate index directly
    # so no extra full-history grouping passes are needed (Part L).
    qualifying = {day for day, (m, _) in day_map.items()
                  if m >= STREAK_QUALIFYING_MIN_MINUTES}
    result.current_streak_days = current_streak(sessions, today=today,
                                                qualifying=qualifying)
    result.longest_streak_days = longest_streak(sessions, qualifying=qualifying)

    result.total_sessions = len(sessions)
    result.active_days = len(day_map)
    if result.active_days:
        result.average_daily_minutes = total_minutes / result.active_days

    # Medal counts: highest medal per day only (FR-46 / A-11)
    for agg in aggregates.values():
        if agg.medal == GOLD:
            result.gold_count += 1
        elif agg.medal == SILVER:
            result.silver_count += 1
        elif agg.medal == BRONZE:
            result.bronze_count += 1

    if notes is not None:
        result.total_notes = len(list(notes))

    return result


# --- v2: Review metrics (REV-1..REV-3, UI-24) --------------------------------
# Pure arithmetic over the coaching log — works with zero AI code and shows
# a friendly empty state while ``coach_sessions`` is still empty.

WEAK_SCORE_THRESHOLD = 2      # REV-1: sources with score >= 2 list in Review
_CONFIDENCE_PENALTY = {"low": 2, "medium": 1, "high": 0}   # REV-1 weights
_CONFIDENCE_VALUE = {"low": 0, "medium": 1, "high": 2}     # for averages


@dataclass
class WeakSource:
    """One Review row (UI-24): per-source weak-topic totals (REV-1/REV-2)."""

    source_id: str
    kind: str                  # "note" | "document"
    label: str
    score: int = 0             # summed weak points over its questions
    question_count: int = 0
    confidence_sum: int = 0    # sum of numeric confidences (rated only)
    rated_count: int = 0       # questions with a confidence rating
    latest_session_id: str | None = None   # REV-2 click target

    @property
    def average_confidence(self) -> float | None:
        """Mean confidence 0..2 (None when nothing was ever rated)."""
        if self.rated_count == 0:
            return None
        return self.confidence_sum / self.rated_count


@dataclass
class CoachActivity:
    """Coach activity cards (REV-3), derived from every persisted turn."""

    sessions: int = 0
    questions_asked: int = 0
    questions_answered: int = 0
    hints_used: int = 0
    revisions: int = 0
    confidence_counts: dict[str, int] = field(
        default_factory=lambda: {"low": 0, "medium": 0, "high": 0})

    @property
    def answered_ratio(self) -> float:
        if self.questions_asked == 0:
            return 0.0
        return self.questions_answered / self.questions_asked


@dataclass
class ReviewMetrics:
    """Everything the Stats Review section needs (UI-24)."""

    weak_sources: list[WeakSource] = field(default_factory=list)  # score >= 2
    all_sources: list[WeakSource] = field(default_factory=list)   # sorted desc
    activity: CoachActivity = field(default_factory=CoachActivity)

    @property
    def has_coach_data(self) -> bool:
        return self.activity.sessions > 0


def _question_weak_points(question) -> int:
    """REV-1 per-question weak points.

    confidence low +2, medium +1; revisions >= 1 adds +1; hints >= 2 adds +1.
    """
    score = _CONFIDENCE_PENALTY.get(question.confidence or "", 0)
    if len(question.revisions) >= 1:
        score += 1
    if question.hints_seen >= 2:
        score += 1
    return score


def compute_review_metrics(
    coach_sessions: Iterable[CoachSession],
) -> ReviewMetrics:
    """Derive weak-topic scores and coach activity from the coaching log.

    Pure function (INT-2): no file access, no AI.  Sources are keyed by the
    selection snapshot stored inside each session (SEL-3), so labels stay
    readable even after the underlying note/document was deleted (ERR-33).
    Follow-up child questions count toward their parent's source set, since
    they share the same pack (COACH-8).
    """
    metrics = ReviewMetrics()
    activity = metrics.activity
    per_source: dict[tuple[str, str], WeakSource] = {}
    # Latest session containing each source key (REV-2 click target).
    latest_by_source: dict[tuple[str, str], tuple[date, str]] = {}

    for session in coach_sessions:
        activity.sessions += 1
        keys = [(ref.kind, ref.id) for ref in session.sources]
        for ref in session.sources:
            key = (ref.kind, ref.id)
            entry = per_source.get(key)
            if entry is None:
                entry = WeakSource(source_id=ref.id, kind=ref.kind,
                                   label=ref.label)
                per_source[key] = entry
            stamp = session.updated_at_local.date()
            known = latest_by_source.get(key)
            if known is None or known[0] <= stamp:
                latest_by_source[key] = (stamp, session.id)

        for question in session.questions:
            activity.questions_asked += 1
            if question.first_answer is not None:
                activity.questions_answered += 1
            activity.hints_used += max(0, question.hints_seen)
            activity.revisions += len(question.revisions)
            if question.confidence in _CONFIDENCE_VALUE:
                activity.confidence_counts[question.confidence] += 1

            weak = _question_weak_points(question)
            for key in keys:
                entry = per_source[key]
                entry.question_count += 1
                entry.score += weak
                if question.confidence in _CONFIDENCE_VALUE:
                    entry.confidence_sum += _CONFIDENCE_VALUE[
                        question.confidence]
                    entry.rated_count += 1

    ordered = sorted(per_source.values(),
                     key=lambda s: (-s.score, s.label.lower()))
    for entry in ordered:
        latest = latest_by_source.get((entry.kind, entry.source_id))
        if latest is not None:
            entry.latest_session_id = latest[1]

    metrics.all_sources = ordered
    metrics.weak_sources = [s for s in ordered
                            if s.score >= WEAK_SCORE_THRESHOLD]
    return metrics
