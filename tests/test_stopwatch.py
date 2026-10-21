"""Tests for the sidebar stopwatch widget.

Covers both modes:
- classic stopwatch behaviour incl. the cheat-safe auto-log-on-stop rule
  (real elapsed time is credited, rounded down to 10-minute steps);
- the new study-session countdown mode (start/pause/cancel/complete)
  where XP/time is only granted when the countdown truly reaches zero.

The heavy CTk widget construction is stubbed out so these run headlessly;
the Tk-dependent dialog test is skipped when no display is available.
"""

from __future__ import annotations

import pytest

ctk = pytest.importorskip("customtkinter")

from app.gui.widgets import stopwatch as sw  # noqa: E402
from app.state.app_state import AppState  # noqa: E402


class _Stub:
    """Minimal stand-in for CTk widgets during headless construction."""

    def __init__(self, *args, **kwargs):
        pass

    def pack(self, *args, **kwargs):
        pass

    def configure(self, *args, **kwargs):
        pass


class _PackForgetStub(_Stub):
    def pack_forget(self, *args, **kwargs):
        pass


def _make_widget(monkeypatch):
    monkeypatch.setattr(sw.ctk.CTkFrame, "__init__", _Stub.__init__)
    monkeypatch.setattr(sw.ctk.CTkFrame, "pack", _Stub.pack)
    monkeypatch.setattr(sw.ctk.CTkFrame, "configure", _Stub.configure)
    monkeypatch.setattr(sw.ctk.CTkLabel, "__init__", _Stub.__init__)
    monkeypatch.setattr(sw.ctk.CTkLabel, "pack", _Stub.pack)
    monkeypatch.setattr(sw.ctk.CTkLabel, "configure", _Stub.configure)
    monkeypatch.setattr(sw.ctk.CTkButton, "__init__", _Stub.__init__)
    monkeypatch.setattr(sw.ctk.CTkButton, "pack", _PackForgetStub.pack)
    monkeypatch.setattr(sw.ctk.CTkButton, "pack_forget",
                        _PackForgetStub.pack_forget)
    monkeypatch.setattr(sw.ctk.CTkButton, "configure", _Stub.configure)
    monkeypatch.setattr(sw.ctk.CTkFont, "__init__", _Stub.__init__)
    monkeypatch.setattr(sw.StopwatchWidget, "winfo_toplevel",
                        lambda self: None)
    monkeypatch.setattr(sw.StopwatchWidget, "after",
                        lambda self, ms, fn: "job")
    monkeypatch.setattr(sw.StopwatchWidget, "after_cancel",
                        lambda self, job: None)
    state = AppState()
    w = sw.StopwatchWidget(master=None, app_state=state)
    w._state = state
    return w


@pytest.fixture
def widget(monkeypatch):
    return _make_widget(monkeypatch)


# --- formatting -------------------------------------------------------------

def test_format_zero_defaults():
    assert sw.format_stopwatch(0) == "0:00:00"


def test_format_hms():
    assert sw.format_stopwatch(2 * 3600 + 35 * 60 + 12) == "2:35:12"
    assert sw.format_stopwatch(65) == "0:01:05"


def test_format_minutes_words():
    assert sw.format_minutes(150) == "2 hours 30 minutes"
    assert sw.format_minutes(60) == "1 hour"
    assert sw.format_minutes(10) == "10 minutes"


# --- default / controls -----------------------------------------------------

def test_default_display_is_zero(widget):
    assert widget._display_text() == "0:00:00"
    assert widget._running is False


def test_start_sets_running_and_disables_start(widget):
    widget._on_start()
    assert widget._running is True
    assert widget._segment_start is not None


def test_reset_clears_everything(widget):
    widget._accumulated_s = 1234.0
    widget._on_start()
    widget._on_reset()
    assert widget._running is False
    assert widget._accumulated_s == 0.0
    assert widget._display_text() == "0:00:00"


# --- auto-logging on stop ---------------------------------------------------

def _finish(widget, seconds):
    """Simulate a run of exactly ``seconds`` and press Stop."""
    widget._on_start()
    widget._segment_start -= seconds          # rewind the monotonic anchor
    widget._on_stop()


def test_stop_under_10_minutes_does_not_log(widget):
    _finish(widget, 9 * 60 + 30)             # 9:30 — below threshold
    assert widget._state.dataset.sessions == []
    # reading is kept so the user can resume
    assert widget._display_text() == "0:09:30"


def test_stop_exactly_10_minutes_logs_ten(widget):
    _finish(widget, 10 * 60)                 # 10:00 counts as over 10 mins
    sessions = widget._state.dataset.sessions
    assert len(sessions) == 1
    assert sessions[0].duration_minutes == 10


def test_stop_over_10_minutes_logs_rounded_down(widget):
    _finish(widget, 12 * 60 + 40)            # 12:40 -> 10 minutes
    sessions = widget._state.dataset.sessions
    assert len(sessions) == 1
    assert sessions[0].duration_minutes == 10
    assert widget._display_text() == "0:00:00"   # resets after logging


def test_stop_2h35m12s_logs_two_hours_thirty(widget):
    _finish(widget, 2 * 3600 + 35 * 60 + 12)  # 2:35:12 -> 2 h 30 min
    sessions = widget._state.dataset.sessions
    assert len(sessions) == 1
    assert sessions[0].duration_minutes == 150
    assert sessions[0].source == "custom"


def test_stop_multiple_segments_accumulate(widget):
    widget._on_start()
    widget._segment_start -= 6 * 60           # first segment: 6 min
    widget._on_stop()                         # under threshold: kept
    widget._on_start()
    widget._segment_start -= 7 * 60           # second segment: 7 min
    widget._on_stop()                         # total 13 min -> logs 10
    sessions = widget._state.dataset.sessions
    assert len(sessions) == 1
    assert sessions[0].duration_minutes == 10
    assert widget._display_text() == "0:00:00"


def test_stop_message_dialog_shown(monkeypatch, widget):
    shown = {}
    monkeypatch.setattr(sw, "show_message",
                        lambda parent, title, msg: shown.update(
                            title=title, msg=msg))
    _finish(widget, 2 * 3600 + 35 * 60 + 12)
    assert shown["title"] == "Session logged"
    assert "2 hours 30 minutes" in shown["msg"]


# --- countdown mode (study sessions started from the Study Tracker) ---------

def test_start_countdown_switches_mode(widget):
    assert widget.start_countdown(60) is True
    assert widget.countdown_active is True
    # displays remaining time, counting DOWN from the planned length
    assert widget._display_text() == "1:00:00"
    # nothing is recorded at start time — that was the whole point
    assert widget._state.dataset.sessions == []


def test_start_countdown_refused_while_active(widget):
    assert widget.start_countdown(60) is True
    # spamming starts cannot stack sessions
    assert widget.start_countdown(120) is False
    assert widget.start_countdown(60) is False
    assert widget._cd_total_s == 60 * 60
    assert widget._state.dataset.sessions == []


def test_start_countdown_rejects_bad_input(widget):
    assert widget.start_countdown(0) is False
    assert widget.start_countdown(-5) is False
    assert widget.start_countdown("60") is False
    assert not widget.countdown_active


def test_countdown_completion_records_session(widget, monkeypatch):
    shown = {}
    monkeypatch.setattr(sw, "show_message",
                        lambda parent, title, msg: shown.update(
                            title=title, msg=msg))
    widget.start_countdown(60)
    widget._cd_segment_start -= 60 * 60      # simulate an hour passing
    widget._tick()                           # tick sees remaining <= 0
    sessions = widget._state.dataset.sessions
    assert len(sessions) == 1
    assert sessions[0].duration_minutes == 60
    assert sessions[0].source == "timer"
    assert shown["title"] == "Session complete"
    assert "1 hour" in shown["msg"]
    # widget reverts to normal stopwatch mode, display reset
    assert not widget.countdown_active
    assert widget._display_text() == "0:00:00"


def test_pause_freezes_remaining_time(widget):
    widget.start_countdown(60)
    widget._cd_segment_start -= 10 * 60      # 10 min genuinely elapsed
    widget._on_pause_toggle()                # pause
    frozen = widget._remaining_s()
    assert abs(frozen - 50 * 60) < 1
    # while paused, rewinding the anchor changes nothing (no segment runs)
    widget._cd_running is False
    assert widget._remaining_s() == frozen
    # resume: the banked balance keeps decreasing from the frozen value
    widget._on_pause_toggle()
    widget._cd_segment_start -= 5 * 60
    assert abs(widget._remaining_s() - 45 * 60) < 1


def test_paused_time_does_not_complete_session(widget):
    widget.start_countdown(60)
    widget._on_pause_toggle()
    # huge amount of wall-clock time "passes" while paused
    widget._cd_segment_start = None
    widget._tick()                           # tick returns early: not running
    assert widget._state.dataset.sessions == []
    assert widget.countdown_active


def test_cancel_grants_nothing(widget):
    widget.start_countdown(120)
    widget._cd_segment_start -= 119 * 60     # almost done…
    widget.cancel_countdown()                # …but abandoned
    assert widget._state.dataset.sessions == []
    assert not widget.countdown_active
    assert widget._display_text() == "0:00:00"


def test_reset_cancels_countdown(widget):
    widget.start_countdown(60)
    widget._on_reset()                       # Reset acts as Cancel here
    assert widget._state.dataset.sessions == []
    assert not widget.countdown_active


def test_stopwatch_controls_ignored_during_countdown(widget):
    widget.start_countdown(60)
    widget._accumulated_s = 30 * 60          # pretend a prior stopwatch run
    widget._on_start()                       # must be ignored
    widget._on_stop()                        # must be ignored
    assert widget._running is False
    assert widget.countdown_active
    assert widget._state.dataset.sessions == []
