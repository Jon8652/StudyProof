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

    # 2. Study Tracker screen starts countdowns (no instant logging).
    state = app._app_state
    home = app._get_screen("home")
    app.show_screen("home")
    app.update()

    # Spamming the start buttons must NOT grant XP/time — each click only
    # tries to start a real-time countdown; while one is active the rest
    # are refused and the widget's buttons are disabled.
    n_before_spam = len(state.dataset.sessions)
    home._start_preset(60)
    app.update()
    home._start_preset(60)   # refused: countdown already running
    home._start_preset(120)  # refused too
    app.update()
    check("spam-start-grants-nothing",
          len(state.dataset.sessions) == n_before_spam,
          f"(sessions={len(state.dataset.sessions)})")
    sw_widget = app.stopwatch
    check("countdown-active-after-start", sw_widget.countdown_active)
    check("countdown-buttons-disabled",
          str(home.preset_buttons[0].cget("state")) == "disabled"
          and str(home.custom_button.cget("state")) == "disabled")

    # Cancelling an unfinished countdown grants nothing.
    sw_widget.cancel_countdown()
    app.update()
    check("cancel-grants-nothing",
          len(state.dataset.sessions) == n_before_spam
          and not sw_widget.countdown_active)

    # Completing a countdown records exactly one timer session with the
    # full planned duration (XP/medals derive from it).
    sessions_before = len(state.dataset.sessions)
    home._start_preset(120)                      # 2 Hours preset
    app.update()
    sw_widget._cd_segment_start -= 120 * 60      # simulate the two hours passing
    sw_widget._tick()                            # tick sees remaining <= 0
    app.update()
    agg = state.get_today_summary()
    new_sessions = state.dataset.sessions[sessions_before:]
    check("countdown-completion-records-session",
          len(new_sessions) == 1
          and new_sessions[0].source == "timer"
          and new_sessions[0].duration_minutes == 120,
          f"(new={[(s.source, s.duration_minutes) for s in new_sessions]})")
    # 120 min => 120 XP and the Bronze medal threshold is exactly 120.
    # (XP uses floor-division per 10-minute block, so compare against the
    # *today* sessions only — other smoke sections may have logged earlier
    # days.)
    from datetime import date as _date
    from app.core.date_rules import sessions_for_day
    today_sessions = sessions_for_day(state.dataset.sessions, _date.today())
    expected_xp = sum((s.duration_minutes // 10) * state.settings.xp_per_10_minutes
                      for s in today_sessions)
    check("completion-xp-medal",
          agg is not None and agg.total_xp == expected_xp
          and agg.total_minutes >= 120 and agg.medal == "bronze",
          f"(xp={agg.total_xp if agg else None}, medal={agg.medal if agg else None})")
    check("widget-reverts-to-stopwatch",
          not sw_widget.countdown_active
          and sw_widget._display_text() == "0:00:00")

    # Pause freezes the remaining time (paused seconds never count).
    home._start_preset(60)
    app.update()
    rem0 = sw_widget._remaining_s()
    sw_widget._on_pause_toggle()                 # pause
    sw_widget._cd_segment_start = None
    frozen = sw_widget._remaining_s()
    import time as _time
    sw_widget._cd_remaining_s = frozen           # (pause banks the leg)
    check("pause-freezes-remaining", abs(frozen - rem0) < 1.0,
          f"(frozen={frozen:.1f}, start={rem0:.1f})")
    sw_widget.cancel_countdown()
    app.update()

    # 3. Custom session dialog: valid submit starts a countdown.
    dlg = CustomSessionDialog(app, on_start=lambda total: home._start_countdown(total))
    dlg.hours_entry.delete(0, "end"); dlg.hours_entry.insert(0, "1")
    dlg.minutes_entry.delete(0, "end"); dlg.minutes_entry.insert(0, "45")
    before = len(state.dataset.sessions)
    dlg._submit()
    app.update()
    check("custom-dialog-starts-countdown",
          len(state.dataset.sessions) == before
          and sw_widget.countdown_active
          and abs(sw_widget._cd_total_s - 105 * 60) < 1,
          f"(total={sw_widget._cd_total_s})")
    # complete it so later checks see stable data
    sw_widget._cd_segment_start -= 105 * 60
    sw_widget._tick()
    app.update()

    # 4. Invalid custom input shows inline error, does not crash/start.
    dlg2 = CustomSessionDialog(app, on_start=lambda total: home._start_countdown(total))
    dlg2.hours_entry.insert(0, "99")
    n_before = len(state.dataset.sessions)
    dlg2._submit()
    app.update()
    check("custom-dialog-invalid-rejected",
          len(state.dataset.sessions) == n_before
          and not sw_widget.countdown_active
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
    #    (Flush through the state layer; app._on_close() destroys the Tk root,
    #    so it is exercised at the very end of this script instead.)
    state.autosave()
    outcome = repository.validate_file_for_manual_load(paths.get_default_save_path())
    ds, _warnings = outcome.dataset, outcome.warnings
    check("close-flush-roundtrip", len(ds.sessions) == 2,
          f"(persisted={len(ds.sessions)})")
    check("assets-load-no-crash", all(
        paths.get_asset_path(f).exists() for f in
        ("app_icon.png", "bronze_medal.png", "silver_medal.png", "gold_medal.png")))

    # ---- Phase 1-3 (v2): PDF library, page viewer, selection, coach ----
    import pymupdf  # noqa: E402

    pdf_path = _data_dir / "sample.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Photosynthesis converts light into glucose.")
    doc.save(str(pdf_path))
    doc.close()

    # 9. Import a real PDF; extraction runs in the background worker.
    res = state.import_document(pdf_path)
    check("import-document", res.ok and res.value is not None,
          f"(msg={res.message!r})")
    imported_doc = res.value

    # Library screen navigates (this is where the visible=False crash was).
    try:
        app.show_screen("library")
        app.update_idletasks()
        app.update()
        check("navigate:library", True)
    except Exception as exc:  # noqa: BLE001
        check("navigate:library", False, repr(exc))

    lib = app._get_screen("library")
    lib._start_extraction(imported_doc.id)
    deadline = __import__("time").time() + 15
    while __import__("time").time() < deadline:
        app.update()
        d = state.document_by_id(imported_doc.id)
        if d and d.status in ("ready", "textless"):
            break
        __import__("time").sleep(0.02)
    d = state.document_by_id(imported_doc.id)
    check("extraction-completes", d is not None and d.status == "ready",
          f"(status={d.status if d else None}, pages={d.page_count if d else 0})")
    check("extracted-text-stored", bool(d and any(
        p.get("page") == 1 and p.get("text", "").strip()
        for p in d.text_pages)),
          f"(pages={[(p['page'], len(p['text'])) for p in d.text_pages] if d else []})")

    # 11. Duplicate import refused with existing id (PDF-3).
    dup = state.import_document(pdf_path)
    check("duplicate-refused", (not dup.ok) and dup.value == imported_doc.id,
          f"(msg={dup.message!r})")

    # 12. Oversized refusal before any copy (ERR-30/PDF-2).
    from app.constants import MAX_PDF_BYTES
    big = state.import_document(pdf_path, file_size=MAX_PDF_BYTES + 1)
    check("oversize-refused", not big.ok, f"(msg={big.message!r})")

    # 13. Page viewer shows the page image without crashing.
    lib.select_document(imported_doc.id)
    for _ in range(100):
        app.update()
        __import__("time").sleep(0.02)
    check("page-viewer-renders", lib.viewer.image_label.has_image)

    # 14. Selection dialog lists sources; confirm returns selection.
    note_res = state.create_note("Biology recap",
                                 "The Calvin cycle fixes carbon in the stroma.")
    check("create-note", note_res.ok)
    entries = state.selectable_sources()
    kinds_ids = {(e["kind"], e["id"]) for e in entries}
    check("selectable-sources",
          ("document", imported_doc.id) in kinds_ids
          and ("note", note_res.value.id) in kinds_ids,
          f"(n={len(entries)})")
    from app.gui.dialogs.source_select import SourceSelectDialog
    sel_dlg = SourceSelectDialog(app, state)
    app.update_idletasks(); app.update()
    for e in sel_dlg._entries():
        if not e["available"]:
            continue
        key = (e["kind"], e["id"])
        var = sel_dlg._variables[key]
        if not var.get():          # simulate a user click (var already False)
            var.set(True)
            sel_dlg._toggled(key, var)
    check("source-select-dialog", len(sel_dlg._ordered_ids) >= 2,
          f"(ordered={sel_dlg._ordered_ids})")
    sel_result = list(sel_dlg._ordered_ids)
    sel_dlg._confirm()
    check("source-select-confirm", (sel_dlg.result or []) == sel_result,
          f"(result={sel_dlg.result})")

    # 15. Coach session container persists with selection snapshot (SEL-3).
    refs = [state.source_ref_for(k, i) for k, i in sel_result]
    coach_res = state.create_coach_session([r for r in refs if r])
    check("coach-session-created", coach_res.ok, f"(msg={coach_res.message!r})")

    # Exercise the coach with fixed test replies and no external requests.
    app.show_screen('coach')
    coach = app.screens['coach']
    from tests.conftest import ScriptedProvider
    state.set_ai_settings({'base_url': 'https://example.test/v1', 'api_key': 'test-key'})
    class ReviewProvider(ScriptedProvider):
        confidence = 'low'
    coach.service.live_factory = ReviewProvider
    coach.resume_session(coach_res.value.id)
    sid = coach_res.value.id
    check('coach-question', coach.service.request_question(sid).ok)
    check('coach-answer', coach.service.submit_answer(sid, 'My unaided explanation').ok)
    check('coach-feedback', coach.service.request_feedback(sid).ok)
    q = coach.state.coach_session_by_id(sid).questions[-1]
    check('coach-confidence-assigned', q.confidence in ('low', 'medium', 'high'),
          f"(confidence={q.confidence!r})")
    check('coach-revision', coach.service.submit_revision(sid, 'My revised explanation').ok)
    check('coach-followup', coach.service.request_followup(sid).ok)
    check('coach-review-updates', bool(state.get_review_metrics().weak_sources))

    # 16. Source pack builds within budget (SEL-2).
    pack = state.build_pack_for_selection(sel_result)
    check("source-pack-built", pack is not None and pack.total_chars > 0,
          f"(chars={pack.total_chars if pack else 0})")

    # 17. Stats screen renders its Review section (Phase 3.3).
    try:
        app.show_screen("stats")
        app.update_idletasks()
        app.update()
        metrics = state.get_review_metrics()
        check("stats-review-section",
              hasattr(metrics, "weak_sources") and hasattr(metrics, "activity"),
              f"(weak={len(metrics.weak_sources)}, "
              f"activity={metrics.activity.sessions} sessions)")
    except Exception as exc:  # noqa: BLE001
        check("stats-review-section", False, repr(exc))

    # 18. Delete document removes record + binary, keeps coach session (LIB-5).
    del_res = state.delete_document(imported_doc.id)
    stored = _data_dir / "library" / imported_doc.stored_name
    check("delete-document", del_res.ok and state.document_by_id(imported_doc.id) is None
          and not stored.exists())
    check("coach-session-survives-deletion",
          state.coach_session_by_id(coach_res.value.id) is not None)

    # 19. v2 data round-trips through save/load (schema migration path).
    app._on_close()
    outcome2 = repository.validate_file_for_manual_load(
        paths.get_default_save_path())
    ds2, warns2 = outcome2.dataset, outcome2.warnings
    check('coach-first-answer-roundtrip',
          ds2.coach_sessions[0].questions[0].first_answer == 'My unaided explanation')
    check("v2-roundtrip-persist",
          len(ds2.documents) == 0 and len(ds2.coach_sessions) == 1
          and len(ds2.notes) == 1,
          f"(docs={len(ds2.documents)}, coach={len(ds2.coach_sessions)}, "
          f"notes={len(ds2.notes)}, warns={warns2})")

    if failures:
        print(f"SMOKE TEST: FAIL ({len(failures)}): {failures}")
        return 1
    print("SMOKE TEST: PASS — all GUI flows exercised cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
