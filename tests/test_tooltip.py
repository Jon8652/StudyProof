"""Tooltip regression tests (run under a display; skip cleanly without one).

Covers the two failure modes from the bug report:
* popups that stayed visible after the pointer left the widget, and
* silent whole-process crashes caused by creating/destroying a mapped
  top-level on every hover (Tk/Windows).  The design now reuses ONE
  withdrawn-by-default window, so these tests assert both behaviour and
  that repeated hover churn never creates extra toplevels.
"""
import time
import tkinter

import pytest

from app.gui.widgets.tooltip import Tooltip, _HOVER_DELAY_MS


@pytest.fixture()
def root():
    try:
        root = tkinter.Tk()
    except tkinter.TclError as exc:      # no display available
        pytest.skip(f'Tk display unavailable: {exc}')
    yield root
    Tooltip.dismiss_all()
    root.destroy()


def _wait(app, predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.update()
        time.sleep(0.01)
    return predicate()


def _armed(tip, viewable=True):
    """Make the widget *appear* mapped/viewable to Tk (deiconify the root),
    then arm the tip's hover timer directly — this is what a real <Enter>
    does; we avoid synthesising events so the test never depends on where
    the physical pointer happens to be."""
    tip.widget.winfo_viewable = lambda: 1 if viewable else 0
    tip._enter_job = None
    tip._show_now()


def _popup_viewable():
    window = Tooltip._shared_window
    try:
        return window is not None and window.winfo_exists() and bool(window.winfo_viewable())
    except tkinter.TclError:
        return False


class FakePointer:
    """Patch the two pointer probes so we can script where the mouse is."""

    def __init__(self, app, widget, over=True):
        self.app, self.widget = app, widget
        self.over = over                 # pointer resting on the widget?
        self._xy = widget.winfo_pointerxy
        self._containing = widget.winfo_containing
        widget.winfo_pointerxy = lambda: (0, 0)
        widget.winfo_containing = self._containing_patch

    def _containing_patch(self, x, y):
        if not self.over:
            return self.app              # pointer "elsewhere": the root
        return self.widget               # pointer "on" the widget

    def move_away(self):
        self.over = False

    def restore(self):
        self.widget.winfo_pointerxy = self._xy
        self.widget.winfo_containing = self._containing


def test_shows_only_after_hover_delay(root):
    button = tkinter.Button(root, text='B')
    tip = Tooltip(button, 'Finish this question and get feedback first.')
    pointer = FakePointer(root, button)
    try:
        # Enter alone must NOT pop the window — only arm the delay timer.
        tip._on_enter()
        root.update()
        assert not _popup_viewable(), 'popup must not appear instantly'
        assert tip._enter_job is not None, 'hover timer should be armed'
        # When the timer fires with the pointer resting on the widget, show.
        _armed(tip)
        root.update()
        assert _popup_viewable(), 'popup should appear once the pointer rests'
        assert Tooltip._active is tip
    finally:
        pointer.restore()
        tip._on_destroy()
        button.destroy()


def test_no_popup_when_pointer_already_left(root):
    """The stale-Enter case that produced lingering popups: by the time the
    delay timer fires the pointer is gone — nothing may be shown."""
    button = tkinter.Button(root, text='B')
    tip = Tooltip(button, 'explanation')
    pointer = FakePointer(root, button, over=False)
    try:
        tip._on_enter()
        time.sleep(_HOVER_DELAY_MS / 1000 + 0.05)
        root.update()
        assert not _popup_viewable(), 'must not pop up for a stale hover'
        assert Tooltip._active is None
    finally:
        pointer.restore()
        tip._on_destroy()
        button.destroy()


def test_hides_when_pointer_leaves(root):
    button = tkinter.Button(root, text='B')
    tip = Tooltip(button, 'explanation')
    pointer = FakePointer(root, button)
    try:
        _armed(tip)
        root.update()
        assert _popup_viewable()
        # Explicit Leave event hides immediately.
        tip._on_leave()
        root.update()
        assert not _popup_viewable()
        # And even with NO Leave event at all, the poll timer notices the
        # pointer moved away and dismisses the popup on its own.
        _armed(tip)
        root.update()
        assert _popup_viewable()
        pointer.move_away()
        assert _wait(root, lambda: not _popup_viewable(), timeout=2), \
            'poll must dismiss the popup when the dropped-Leave case occurs'
    finally:
        pointer.restore()
        tip._on_destroy()
        button.destroy()


def test_set_text_while_showing_dismisses_popup(root):
    button = tkinter.Button(root, text='B')
    tip = Tooltip(button, 'first explanation')
    pointer = FakePointer(root, button)
    try:
        _armed(tip)
        root.update()
        assert _popup_viewable()
        tip.set_text('')                 # e.g. the gate opened in refresh()
        root.update()
        assert not _popup_viewable(), 'stale text must never linger after set_text'
    finally:
        pointer.restore()
        tip._on_destroy()
        button.destroy()


def test_empty_text_never_shows(root):
    button = tkinter.Button(root, text='B')
    tip = Tooltip(button, '')
    pointer = FakePointer(root, button)
    try:
        tip._on_enter()
        time.sleep(_HOVER_DELAY_MS / 1000 + 0.05)
        root.update()
        assert not _popup_viewable()
        tip._show_now()                  # even a forced show must no-op
        assert not _popup_viewable()
    finally:
        pointer.restore()
        tip._on_destroy()
        button.destroy()


def test_rapid_hover_churn_reuses_one_window_and_survives(root):
    """Regression for the silent crash: hovering many widgets repeatedly used
    to create/destroy CTkToplevels per hover.  Now exactly one shared window
    exists no matter how much churn happens, and nothing raises out of the
    event handlers even when widgets are destroyed mid-hover."""
    buttons = [tkinter.Button(root, text=f'B{i}') for i in range(6)]
    tips = [Tooltip(b, f'explanation {i}') for i, b in enumerate(buttons)]
    pointers = [FakePointer(root, b) for b in buttons]
    try:
        for cycle in range(8):
            for tip in tips:
                tip._on_enter()
                _armed(tip)              # simulate the delay firing
                tip._on_leave()          # full enter/show/leave churn
                root.update()
        # Repeatedly destroy widgets while their tooltips may be showing.
        for tip, button in zip(tips[:3], buttons[:3]):
            _armed(tip)
            root.update()
            button.destroy()             # fires <Destroy> -> must not raise
            root.update()
        assert Tooltip._shared_window is not None
        windows = [w for w in root.winfo_children()
                   if isinstance(w, tkinter.Toplevel)]
        assert len(windows) == 1, 'hover churn must never create extra popups'
        Tooltip.dismiss_all()
        assert not _popup_viewable()
    finally:
        for p in pointers:
            p.restore()
        for tip in tips:
            tip._on_destroy()
        for b in buttons:
            if b.winfo_exists():
                b.destroy()


def test_citation_chip_imports_shared_tooltip():
    """citation_chip must re-export the single Tooltip implementation — the
    old duplicate class there was the source of the lingering popups."""
    from app.gui.widgets import citation_chip
    assert citation_chip.Tooltip is Tooltip


def test_popup_font_matches_ctk_default(root):
    """The shared popup label must render at CTk's default 13pt size — the
    refactor briefly used a bare tk.Label (Tk's ~9pt system default), which
    made hover text noticeably smaller than before."""
    from app.gui.widgets.tooltip import _FONT_SIZE

    button = tkinter.Button(root, text='B')
    button.pack()
    Tooltip(button, 'finish this question and get feedback first')
    root.update_idletasks()
    window = Tooltip._ensure_window()
    font = Tooltip._shared_label.cget('font')
    size = int(font[-1]) if isinstance(font, (tuple, list)) else int(str(font).split()[-1])
    assert size >= _FONT_SIZE, f'popup font too small: {font!r}'
