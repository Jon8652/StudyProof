"""Tests for the sidebar stopwatch widget (formatting + auto-log rules).

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


def _make_widget(monkeypatch):
    monkeypatch.setattr(sw.ctk.CTkFrame, "__init__", _Stub.__init__)
    monkeypatch.setattr(sw.ctk.CTkFrame, "pack", _Stub.pack)
    monkeypatch.setattr(sw.ctk.CTkFrame, "configure", _Stub.configure)
    monkeypatch.setattr(sw.ctk.CTkLabel, "__init__", _Stub.__init__)
    monkeypatch.setattr(sw.ctk.CTkLabel, "pack", _Stub.pack)
    monkeypatch.setattr(sw.ctk.CTkLabel, "configure", _Stub.configure)
    monkeypatch.setattr(sw.ctk.CTkButton, "__init__", _Stub.__init__)
    monkeypatch.setattr(sw.ctk.CTkButton, "pack", _Stub.pack)
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
