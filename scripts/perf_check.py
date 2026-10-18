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

from app.core.models import Dataset, Session, Settings  # noqa: E402
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
    while len(sessions) < count and cursor <= now:
        duration = rng.choice(durations)
        sessions.append(Session.create(
            duration_minutes=duration,
            source="preset" if rng.random() < 0.7 else "custom",
            now=cursor,
        ))
        # advance 2-8 hours (≈ several sessions per day)
        cursor += timedelta(hours=rng.randint(2, 8), minutes=rng.randint(0, 59))
    return sessions


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="studytracker-perf-")
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

    paths.set_override_data_dir(None)
    if failures:
        print(f"\nRESULT: FAIL ({', '.join(failures)} exceeded budget)")
        return 1
    print("\nRESULT: PASS — app remains responsive at target data size")
    return 0


if __name__ == "__main__":
    sys.exit(main())
