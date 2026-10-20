"""Part L — Performance and data scalability validation.

Generates a realistic 10,000-session history in an isolated temp data dir
and times every operation the plan calls out:

- app startup (save-file load + daily index build)
- Home summary refresh (today aggregate lookup)
- calendar month rendering data (month view slice)
- stats refresh (full StatsResult via the maintained index)
- session deletion (index update + autosave round-trip)
- full index rebuild (load / manual-load only)

Exit code 0 if all operations stay within responsiveness budgets, else 1.
Run:  python scripts/perf_check.py
"""

from __future__ import annotations

import random
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path

# Ensure project root is importable when run as `python scripts/perf_check.py`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.models import (Dataset, Session, Settings, Document, CoachSession,
                             QuestionRecord, SourceRef)  # noqa: E402
from app.core.agent.context import build_source_pack  # noqa: E402
from app.core.agent.grounding import PageIndex  # noqa: E402
from app.core.stats import compute_review_metrics  # noqa: E402
from app.core import pdfx  # noqa: E402
from app.persistence import repository  # noqa: E402
from app.state.app_state import AppState  # noqa: E402
from app.utils import paths  # noqa: E402

TARGET_SESSIONS = 10_000

# Responsiveness budgets (seconds). Personal-desktop targets from Part L:
# "remains responsive", "does not freeze UI noticeably".
BUDGETS = {
    "startup_load": 3.0,
    "home_refresh": 0.05,
    "calendar_month_view": 0.25,
    "stats_refresh": 1.0,
    "session_delete": 1.5,       # includes JSON autosave of full dataset
    "index_rebuild": 3.0,
    "pdf_extract_200_pages": 15.0,   # NFR-12
    "page_render": 0.3,             # NFR-14 at zoom 1.0
    "source_pack_5_documents": 0.1, # NFR-13
    "page_index_50x50": 1.0,        # QA-17: materialise all 2,500 pages
    "review_5000_attempts": 1.0,    # QA-17
}


def timed(fn):
    start = time.perf_counter()
    value = fn()
    return value, time.perf_counter() - start


def generate_sessions(count: int, seed: int = 42) -> list[Session]:
    """Spread ``count`` sessions over ~3 years, some days multi-session."""
    rng = random.Random(seed)
    start_day = date.today() - timedelta(days=3 * 365)
    durations = [15, 25, 30, 45, 60, 90, 120]
    sessions: list[Session] = []
    cursor = datetime.combine(start_day, datetime.min.time()).replace(hour=9)
    now = datetime.now()
    # Spread exactly the requested count over three years, rather than
    # stopping early at ~4,800 records and under-testing the 10,000 target.
    interval = (now - cursor) / max(1, count)
    while len(sessions) < count:
        duration = rng.choice(durations)
        sessions.append(Session.create(
            duration_minutes=duration,
            source="preset" if rng.random() < 0.7 else "custom",
            now=cursor,
        ))
        # advance 2-8 hours (≈ several sessions per day)
        cursor += interval
    return sessions


def check_v2(check, work_dir):
    """Real PDF operations and deterministic, realistically sized v2 data."""
    pymupdf = pdfx._load_pymupdf()
    pdf_path = Path(work_dir) / 'performance-200-pages.pdf'
    sentence = ('Photosynthesis converts sunlight into chemical energy stored in glucose, '
                'while chlorophyll absorbs the light needed for this process. ')
    with pymupdf.open() as pdf:
        for page in range(200):
            pdf.new_page().insert_textbox((50, 50, 550, 780),
                                          f'Biology page {page + 1}\n' + sentence * 18,
                                          fontsize=10)
        pdf.save(str(pdf_path))
    extracted, seconds = timed(lambda: pdfx.extract_document(pdf_path))
    assert extracted.page_count == 200 and all(text for _, text in extracted.pages)
    check('pdf_extract_200_pages', seconds)
    image, seconds = timed(lambda: pdfx.render_page_pixmap(pdf_path, 100, 1.0))
    assert image.width > 0 and image.height > 0
    image.close()
    check('page_render', seconds)

    documents = {}
    for index in range(50):
        document = Document.create(f'Biology {index}.pdf', f'{index}.pdf')
        document.set_extracted_text([(page, sentence * 18) for page in range(1, 51)])
        documents[document.id] = document
    refs = [SourceRef('document', d.id, d.file_name) for d in documents.values()]
    pack, seconds = timed(lambda: build_source_pack(
        [('document', ref.id) for ref in refs[:5]], {}, documents))
    assert pack.total_chars <= 1_000_000 and pack.spans
    check('source_pack_5_documents', seconds)

    def build_index():
        index = PageIndex(refs, {}, documents)
        for ref in refs:
            for page in range(1, 51):
                assert index.page(ref.id, page)
        return index
    index, seconds = timed(build_index)
    assert len(index.cache) == 2500
    check('page_index_50x50', seconds)

    sessions = []
    for number in range(100):
        session = CoachSession.create(refs[number % 50:number % 50 + 1])
        for _ in range(50):
            question = QuestionRecord.create('Explain photosynthesis.')
            question.first_answer, question.confidence = 'Sunlight.', 'low'
            question.hints_seen = 2
            question.revisions = [{'text': 'A revised answer', 'at': '2026-01-01T12:00:00'}]
            session.questions.append(question)
        sessions.append(session)
    metrics, seconds = timed(lambda: compute_review_metrics(sessions))
    assert metrics.activity.questions_answered == 5000
    assert len(metrics.all_sources) == 50
    check('review_5000_attempts', seconds)


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="studyproof-perf-")
    paths.set_override_data_dir(tmp)

    print(f"Generating up to {TARGET_SESSIONS}-session history...")
    sessions, gen_seconds = timed(lambda: generate_sessions(TARGET_SESSIONS))
    print(f"  generated {len(sessions)} sessions in {gen_seconds:.2f}s")

    dataset = Dataset(settings=Settings(), sessions=sessions,
                      created_at=datetime.now(), updated_at=datetime.now())
    _, save_seconds = timed(lambda: repository.save_dataset(dataset))
    print(f"  seeded save file in {save_seconds:.2f}s "
          f"({paths.get_default_save_path().stat().st_size / 1e6:.1f} MB)")

    failures: list[str] = []

    def check(name: str, seconds: float) -> None:
        budget = BUDGETS[name]
        status = "PASS" if seconds <= budget else "FAIL"
        print(f"[{status}] {name}: {seconds * 1000:.1f} ms "
              f"(budget {budget * 1000:.0f} ms)")
        if seconds > budget:
            failures.append(name)

    # 1. Startup: load from disk + rebuild daily index
    state = AppState()
    _, t_load = timed(state.load_startup)
    check("startup_load", t_load)

    today = date.today()

    # 2. Home summary refresh (hot path, must be instant)
    t0 = time.perf_counter()
    for _ in range(100):
        state.get_today_summary()
    check("home_refresh", (time.perf_counter() - t0) / 100)

    # 3. Calendar month view data across the last 12 months
    t0 = time.perf_counter()
    for i in range(12):
        m = ((today.month - 1 - i) % 12) + 1
        y = today.year - ((today.month - 1 - i) // 12)
        state.get_month_view_data(y, m)
    check("calendar_month_view", (time.perf_counter() - t0) / 12)

    # 4. Stats refresh through AppState (uses the maintained index)
    _, t_stats = timed(state.get_stats)
    check("stats_refresh", t_stats)

    # 5. Session deletion (index maintenance + autosave round-trip)
    victim = state.dataset.sessions[len(state.dataset.sessions) // 2]
    result, t_del = timed(lambda: state.delete_session(victim.id))
    assert result.ok, "delete failed during perf run"
    check("session_delete", t_del)

    # 6. Full index rebuild (occurs on load/manual-load only)
    _, t_rebuild = timed(state.rebuild_index)
    check("index_rebuild", t_rebuild)

    # Sanity: optimized paths still produce correct results
    stats = state.get_stats()
    assert stats.total_sessions == len(state.dataset.sessions), "session count drift"
    assert stats.active_days == len(state._day_index), "active-day drift"
    rebuilt = AppState(state.dataset)
    assert rebuilt.get_stats().__dict__ == stats.__dict__, \
        "indexed stats diverge from cold-computed stats"
    print(f"sanity OK — lifetime: {stats.lifetime.minutes} min, "
          f"{stats.lifetime.xp} XP, streak {stats.current_streak_days}, "
          f"longest {stats.longest_streak_days}")

    check_v2(check, tmp)

    paths.set_override_data_dir(None)
    if failures:
        print(f"\nRESULT: FAIL ({', '.join(failures)} exceeded budget)")
        return 1
    print("\nRESULT: PASS — app remains responsive at target data size")
    return 0


if __name__ == "__main__":
    sys.exit(main())
