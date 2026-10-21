"""End-to-end integration tests for countdown study sessions & quick start.

These exercise the *real* production wiring — HomeScreen's 1 Hour / 2 Hours
quick-start buttons, the shared sidebar StopwatchWidget in countdown mode,
AppState.record_timer_session and persistence — with real Tk dialogs and a
pumpable mainloop.  Because nobody wants to wait an hour inside pytest, the
countdowns run at a compressed time scale: we shrink the widget's internal
second→minute conversion so a "60 minute" session actually elapses in ~1
wall-clock second, then let the genuine tick/finish jobs drive completion.
The recording math itself (``int(round(_cd_total_s // 60))``) is untouched,
so what gets credited is exactly what the production code would credit.

Covers, per preset duration (60 and 120 minutes):
- Quick start genuinely RECORDS the full session when the countdown hits
  zero in real time (duration + source), after waiting it out;
- nothing is recorded at start time (anti-cheat);
- cancelling before zero records nothing;
- pausing across the deadline records nothing and stays alive;
- stacking a second session while one runs is refused (UI button disabled
  AND the widget refuses);
- the recorded session survives autosave/reload and shows up in the today
  summary (XP/time path used by the Home screen).
"""

from __future__ import annotations

import time
import types

import pytest

ctk = pytest.importorskip("customtkinter")

from app.gui.screens.home_screen import HomeScreen          # noqa: E402
from app.gui.widgets import stopwatch as sw                 # noqa: E402
from app.gui.widgets.stopwatch import StopwatchWidget       # noqa: E402
from app.state.app_state import AppState                    # noqa: E402


# --------------------------------------------------------------------------
# Harness: real widgets, fake-but-faithful modal dialogs, pumpable mainloop
# --------------------------------------------------------------------------

class _DialogSpy:
    def __init__(self):
        self.messages = []            # (title, msg) tuples in call order

    def show_message(self, parent, title, msg):
        self.messages.append((title, msg))

    def titles(self):
        return [t for t, _ in self.messages]

    def has(self, title, substring=""):
        return any(t == title and substring in m for t, m in self.messages)


class _AutoName(str):
    """A string that vends a fresh, unique Tk widget name on every attribute
    access (``.name`` is read by tkinter's ``Misc.__init__``)."""

    _counter = [0]

    def __getattribute__(self, name):
        if name == "name":
            _AutoName._counter[0] += 1
            return f"stub{self._counter[0]}"
        return super().__getattribute__(name)


class _DialogStub:
    """Duck-typed stand-in for the dialog widgets (CTkToplevel/CTkFrame/
    CTkLabel/CTkButton/CTkFont) built by ``message_dialog.show_message``.

    Every attribute access returns a callable no-op and ``__call__`` does
    the same, so the production dialog code runs its full construction path
    without ever creating a real modal window — which would block inside
    pytest waiting for the user to click OK.  Call results double as widget
    ``master`` args, carrying a unique ``.name`` for tkinter.
    """

    def __getattr__(self, name):
        return self

    def __call__(self, *args, **kwargs):
        return _AutoName()


class _Pump:
    """Drive Tk event processing while sleeping, like a mini mainloop."""

    def __init__(self, root):
        self.root = root

    def sleep(self, total_s: float) -> None:
        deadline = time.monotonic() + total_s
        while time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.005)
        self.root.update()

    def wait_until(self, predicate, timeout_s: float = 3.0) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self.root.update()
            if predicate():
                return True
            time.sleep(0.005)
        self.root.update()
        return bool(predicate())


@pytest.fixture
def harness(monkeypatch):
    """Real Tk root + real StopwatchWidget + real HomeScreen + spied dialogs."""
    from app.gui.dialogs import message_dialog

    # Neutralise modal window creation *inside message_dialog only* —
    # show_message still runs its full construction path against stubs,
    # but real CTk widgets elsewhere are untouched.
    stub = _DialogStub()
    dialog_ctk = types.SimpleNamespace(
        CTkToplevel=stub, CTkFrame=stub, CTkLabel=stub,
        CTkButton=stub, CTkFont=stub)
    monkeypatch.setattr(message_dialog, "ctk", dialog_ctk)

    dialogs = _DialogSpy()
    monkeypatch.setattr(sw, "show_message", dialogs.show_message)
    monkeypatch.setattr("app.gui.screens.home_screen.show_message",
                        dialogs.show_message)
    monkeypatch.setattr("app.gui.screens.home_screen.show_error",
                        lambda parent, msg: dialogs.messages.append(
                            ("error", msg)))

    state = AppState()
    root = ctk.CTk()
    root.withdraw()                       # no window manager fluff needed
    widget = None
    try:
        widget = StopwatchWidget(master=root, app_state=state)
        widget._state = state
        root.stopwatch = widget           # what HomeScreen._stopwatch looks up

        home = HomeScreen(root, state)
        home.winfo_toplevel = lambda: root
        # Mirror what MainWindow does: re-derive every screen (incl. the
        # countdown busy-state of the preset buttons) on any data change.
        state.add_listener(home.refresh)
        pump = _Pump(root)
        yield state, widget, home, dialogs, pump
    finally:
        if widget is not None:
            widget._cancel_tick()
            widget._cancel_cd_finish()
        root.destroy()


class _CompressedClock:
    """A fake ``time.monotonic`` that advances ``scale``x faster.

    Injected as ``sw.time`` so every timestamp the stopwatch takes (start
    anchors, pause banking, remaining-time math) uses compressed "sim time".
    The widget's own logic is untouched: a 60-minute countdown genuinely
    reaches zero after ~1 wall-clock second, and the finish/tick jobs fire
    through the real Tk scheduler.  ``_cd_total_s`` — the value credited at
    completion — is set from the requested minutes, never from the clock,
    so the recorded duration is exactly what production would record.
    """

    def __init__(self, scale: float):
        self._scale = scale
        self._origin = time.monotonic()

    def monotonic(self) -> float:
        return (time.monotonic() - self._origin) * self._scale


def _compress_time(monkeypatch, scale: float = 60.0):
    monkeypatch.setattr(sw, "time", _CompressedClock(scale))


def _sessions(state):
    return [(s.duration_minutes, s.source) for s in state.dataset.sessions]


# --------------------------------------------------------------------------
# The real-time proofs
# --------------------------------------------------------------------------

@pytest.mark.parametrize("minutes,label", [(60, "1 Hour"), (120, "2 Hours")])
def test_quick_start_records_full_session_in_real_time(harness, monkeypatch,
                                                       minutes, label):
    """Pressing the actual preset button and WAITING OUT the (compressed)
    countdown records exactly `minutes` of timer-source study time."""
    state, widget, home, dialogs, pump = harness
    _compress_time(monkeypatch, scale=60.0)  # 60 min of sim-time per wall sec

    idx = {60: 0, 120: 1}[minutes]
    btn = home.preset_buttons[idx]
    assert btn.cget("text") == label

    # Nothing logged at press time (anti-cheat).
    btn.invoke()
    pump.sleep(0.1)
    assert widget.countdown_active
    assert _sessions(state) == []
    assert dialogs.has("Session started", "countdown is running")

    # Wait out the whole session in real time (≈1 s at 60x compression).
    finished = pump.wait_until(lambda: not widget.countdown_active,
                               timeout_s=5.0)
    assert finished, "countdown never completed"
    pump.sleep(0.1)

    assert _sessions(state) == [(minutes, "timer")]
    assert dialogs.has("Session complete", "study session was")
    # Widget reverts cleanly to stopwatch mode.
    assert widget._display_text() == "0:00:00"
    # Home summary now reflects the recorded time.
    agg = state.get_today_summary()
    assert agg is not None and agg.total_minutes == minutes


@pytest.mark.parametrize("minutes", [60, 120])
def test_cancel_before_zero_records_nothing_real_time(harness, monkeypatch,
                                                      minutes):
    state, widget, home, dialogs, pump = harness
    _compress_time(monkeypatch, scale=60.0)

    home._start_preset(minutes)
    assert widget.countdown_active
    pump.sleep(0.3)                         # let it genuinely tick down
    widget.cancel_countdown()
    pump.sleep(0.3)                         # finish job must NOT fire

    assert _sessions(state) == []
    assert not widget.countdown_active
    assert not dialogs.has("Session complete")


@pytest.mark.parametrize("minutes", [60, 120])
def test_paused_across_deadline_records_nothing_real_time(harness, monkeypatch,
                                                          minutes):
    """Pause just before zero, wait past the deadline, stay paused: the
    deferred finish job must not credit a session."""
    state, widget, home, dialogs, pump = harness
    _compress_time(monkeypatch, scale=120.0)   # 2 min of sim-time per sec

    home._start_preset(minutes)
    # Shrink the balance so the deadline lands ~0.15 wall-seconds ahead
    # (36 sim-seconds at 120x compression), then re-arm the finish job.
    widget._cd_remaining_s = 36.0
    widget._cd_segment_start = sw.time.monotonic()
    widget._schedule_cd_finish()
    widget._on_pause_toggle()               # pause before it hits zero

    pump.sleep(1.0)                         # well past the armed deadline
    assert _sessions(state) == []
    assert widget.countdown_active          # still alive, paused

    widget._on_pause_toggle()               # resume honestly finishes
    assert pump.wait_until(lambda: not widget.countdown_active, timeout_s=5.0)
    pump.sleep(0.1)
    assert _sessions(state) == [(minutes, "timer")]


def test_second_quick_start_refused_while_running(harness, monkeypatch):
    state, widget, home, dialogs, pump = harness
    _compress_time(monkeypatch, scale=60.0)

    home.preset_buttons[0].invoke()         # start 1 Hour
    pump.sleep(0.1)
    assert widget.countdown_active

    # UI level: both presets and the custom button are disabled...
    for b in home.preset_buttons:
        assert str(b.cget("state")) == "disabled"
    assert str(home.custom_button.cget("state")) == "disabled"

    # ...and even if invoked programmatically, the widget refuses a stack.
    assert widget.start_countdown(120) is False
    assert _sessions(state) == []

    widget.cancel_countdown()
    pump.sleep(0.1)
    for b in home.preset_buttons:
        assert str(b.cget("state")) == "normal"


def test_recorded_session_survives_reload(harness, monkeypatch):
    """The auto-recorded session is autosaved and reappears after a fresh
    AppState load — i.e. it really hit the persistent dataset."""
    state, widget, home, dialogs, pump = harness
    _compress_time(monkeypatch, scale=60.0)

    home.preset_buttons[1].invoke()         # 2 Hours
    assert pump.wait_until(lambda: not widget.countdown_active, timeout_s=5.0)
    pump.sleep(0.1)
    assert _sessions(state) == [(120, "timer")]

    reloaded = AppState()                   # reads the same isolated data dir
    assert [(s.duration_minutes, s.source)
            for s in reloaded.dataset.sessions] == [(120, "timer")]


def test_custom_session_dialog_starts_countdown_not_instant_log(harness,
                                                                monkeypatch):
    """"Start custom session" also routes through the countdown (no instant
    credit), and completes the same way."""
    state, widget, home, dialogs, pump = harness
    _compress_time(monkeypatch, scale=60.0)

    # Simulate the dialog returning 90 minutes to its callback.
    home._start_countdown(90)
    assert _sessions(state) == []           # nothing logged at start
    assert widget.countdown_active
    assert pump.wait_until(lambda: not widget.countdown_active, timeout_s=5.0)
    pump.sleep(0.1)
    assert _sessions(state) == [(90, "timer")]
