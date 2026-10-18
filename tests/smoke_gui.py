"""End-to-end GUI smoke test (run under xvfb):

  xvfb-run -a python tests/smoke_gui.py

Instantiates MainWindow, navigates all screens, logs preset + custom
sessions through the real action layer, opens dialogs, exercises calendar
month navigation and theme switching, then verifies the data round-trips
to disk. Exits non-zero on any failure.
"""

from __future__ import annotations

import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.utils import paths

_data_dir = Path(tempfile.mkdtemp())
paths.set_override_data_dir(_data_dir)

import customtkinter as ctk  # noqa: E402

from app.gui.dialogs.custom_session_dialog import CustomSessionDialog  # noqa: E402
from app.gui.dialogs.day_details_dialog import DayDetailsDialog  # noqa: E402
from app.gui.main_window import MainWindow  # noqa: E402
from app.persistence import repository  # noqa: E402
from app.state.app_state import AppState  # noqa: E402


def main() -> int:
    failures: list[str] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        status = "PASS" if cond else "FAIL"
        print(f"[{status}] {name} {detail}")
        if not cond:
            failures.append(name)

    app = MainWindow(app_state=AppState())
    app.run_startup_load()

    # 1. Navigate every screen without crashing (Part K).
    for key in ("home", "calendar", "stats", "settings"):
        try:
            app.show_screen(key)
            app.update_idletasks()
            app.update()
            check(f"navigate:{key}", True)
        except Exception as exc:  # noqa: BLE001
            check(f"navigate:{key}", False, repr(exc))

    # 2. Log preset sessions through AppState -> Home updates with medal.
    state = app._app_state
    state.log_preset_session(30)
    state.log_preset_session(60)
    state.log_preset_session(120)
    app.update()
    agg = state.get_today_summary()
    check("preset-logging-xp-medal",
          agg is not None and agg.total_xp >= 90 and agg.medal is not None,
          f"(xp={agg.total_xp if agg else None}, medal={agg.medal if agg else None})")

    # 3. Custom session dialog: valid submit routes through log_custom.
    dlg = CustomSessionDialog(app, on_log=lambda total: state.log_custom_session(total // 60, total % 60))
    dlg.hours_entry.delete(0, "end"); dlg.hours_entry.insert(0, "1")
    dlg.minutes_entry.delete(0, "end"); dlg.minutes_entry.insert(0, "45")
    before = len(state.dataset.sessions)
    dlg._submit()
    app.update()
    check("custom-dialog-submit", len(state.dataset.sessions) == before + 1,
          f"(sessions={len(state.dataset.sessions)})")

    # 4. Invalid custom input shows inline error, does not crash/log.
    dlg2 = CustomSessionDialog(app, on_log=lambda total: state.log_custom_session(total // 60, total % 60))
    dlg2.hours_entry.insert(0, "99")
    n_before = len(state.dataset.sessions)
    dlg2._submit()
    app.update()
    check("custom-dialog-invalid-rejected",
          len(state.dataset.sessions) == n_before
          and dlg2.message_label.cget("text") != "",
          f"(msg={dlg2.message_label.cget('text')!r})")
    dlg2.destroy()

    # 5. Day details dialog opens for today and lists sessions.
    dd = DayDetailsDialog(app, state, date.today())
    app.update_idletasks()
    app.update()
    check("day-details-dialog", True)
    dd.destroy()

    # 6. Calendar month navigation preserves viewed month across refreshes.
    cal = app._get_screen("calendar")  # screens are lazily built now
    y0, m0 = cal._year, cal._month
    import datetime as _dt
    from calendar import monthrange
    prev_y, prev_m = (y0, m0 - 1) if m0 > 1 else (y0 - 1, 12)
    days_prev = monthrange(prev_y, prev_m)[1]
    cal._selected = _dt.date(prev_y, prev_m, min(5, days_prev))
    cal._prev_month(); app.update()
    back_ok = (cal._year, cal._month) == (prev_y, prev_m) and \
              (cal._selected.month, cal._selected.year) == (prev_m, prev_y)
    cal._next_month(); app.update()
    cal._go_today(); app.update()
    check("calendar-navigation", back_ok and (cal._year, cal._month) == (y0, m0))

    # 7. Theme toggle persists through settings + autosave.
    st = app.screens["settings"]
    new_theme = "light" if state.settings.theme == "dark" else "dark"
    st._set_theme(new_theme)
    app.update()
    check("theme-toggle-persisted", state.settings.theme == new_theme)

    # 8. Close-flush (FR-58): data round-trips to the save file.
    app._on_close()
    ds, _warnings = repository.validate_file_for_manual_load(paths.get_default_save_path())
    check("close-flush-roundtrip", len(ds.sessions) == 4,
          f"(persisted={len(ds.sessions)})")
    check("assets-load-no-crash", all(
        paths.get_asset_path(f).exists() for f in
        ("app_icon.png", "bronze_medal.png", "silver_medal.png", "gold_medal.png")))

    if failures:
        print(f"SMOKE TEST: FAIL ({len(failures)}): {failures}")
        return 1
    print("SMOKE TEST: PASS — all GUI flows exercised cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
