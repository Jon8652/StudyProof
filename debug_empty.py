"""Reproduce the failing test with per-observation diagnostics."""
import threading
import time
import tkinter

import pytest
import customtkinter as ctk

from app.gui.main_window import MainWindow
from app.state.app_state import AppState
from app.gui.screens.coach_screen import BOTTOM_EPSILON_PX

from tests.test_coach_gui import GatedProvider, _seed_long_session, _settle, pump


def run_case(display_scale):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(ctk.ScalingTracker, 'get_window_dpi_scaling',
                        classmethod(lambda cls, window: display_scale))
    state = AppState()
    monkeypatch.setattr(state, '_mark_dirty_and_save', lambda: None)
    note = state.create_note('Biology', 'Chlorophyll absorbs sunlight.').value
    state.set_ai_settings({'base_url': 'https://example.test/v1', 'api_key': 'test-key'})
    session = _seed_long_session(state, note, turns=10)
    app = MainWindow(app_state=state)
    release, started = threading.Event(), threading.Event()

    class SlowProvider(GatedProvider):
        gate_kinds = ('feedback',)

    SlowProvider.release, SlowProvider.started = release, started
    app.geometry('900x650+3000+3000')
    app.show_screen('coach')
    screen = app.screens['coach']
    # DIAGNOSTIC: log every pack_forget/pack on the thread's children.
    import traceback as _tb
    for attr in ('pack_forget', 'grid_forget'):
        orig = getattr(screen.thread, attr)
        def make(o, name):
            def wrapper(*a, **k):
                print(f'>>> thread.{name}() called from:')
                _tb.print_stack()
                return o(*a, **k)
            return wrapper
        setattr(screen.thread, attr, make(orig, attr))
    orig_children = screen.thread.winfo_children
    screen.service.live_factory = SlowProvider
    screen.resume_session(session.id)
    assert _settle(app, screen)
    canvas = screen._thread_canvas
    canvas.yview_moveto(.3)
    app.update()
    history = screen.thread.winfo_children()
    visible = []
    log = []

    def observe(*args):
        event = args[0] if args else None
        # Tk has not run the pending geometry pass yet when a Configure
        # callback fires; sync it like app.update_idletasks() would.
        pass  # no idle sync: observe exactly as the test does
        top, height = canvas.canvasy(0), canvas.winfo_height()
        kids = screen.thread.winfo_children()
        rects = [(b.winfo_ismapped(), b.winfo_y(), b.winfo_height()) for b in kids]
        anyvis = any(m and y < top + height and y + h > top for m, y, h in rects)
        visible.append(anyvis)
        log.append({
            'phase': phase[0], 'event': 'pin' if event is None else 'cfg',
            'top': round(top, 1), 'height': height, 'anyvis': anyvis,
            'bbox': canvas.bbox('all'), 'nkids': len(kids),
            'last': rects[-1] if rects else None,
            'mapped': sum(1 for m, _, _ in rects if m),
        })

    original_pin = screen._pin_viewport
    phase = ['resume']

    def pin(target):
        original_pin(target)
        observe()

    monkeypatch.setattr(screen, '_pin_viewport', pin)
    canvas.bind('<Configure>', observe, add='+')
    screen.thread.bind('<Configure>', observe, add='+')
    phase[0] = 'send'
    screen.composer.insert('1.0', 'Why does chlorophyll absorb sunlight? ' * 150)
    screen.send()
    assert screen.busy
    phase[0] = 'waiting'
    pump(app, started.is_set)
    assert _settle(app, screen)
    assert screen.busy
    assert screen._thread_at_bottom()
    sent = screen.thread.winfo_children()[-1]
    assert sent.winfo_height() > canvas.winfo_height()
    bottom_padding = sent.winfo_pixels(sent.pack_info()['pady'])
    padded_bottom = sent.winfo_y() + sent.winfo_height() + bottom_padding
    viewport_bottom = canvas.canvasy(0) + canvas.winfo_height()
    assert abs(padded_bottom - viewport_bottom) <= BOTTOM_EPSILON_PX
    phase[0] = 'reply'
    release.set()
    pump(app, lambda: not screen.busy)
    assert _settle(app, screen)
    assert screen._thread_at_bottom()
    assert sent.winfo_exists()
    bad = [e for e, v in zip(log, visible) if not v]
    print(f"=== scale {display_scale}: {sum(visible)}/{len(visible)} visible; BAD={len(bad)}")
    for e in bad[:12]:
        print('   BAD', e)
    # context around first bad
    if bad:
        i = log.index(bad[0])
        for e in log[max(0, i-4):i+5]:
            print('   ctx', e)
    for s in app.screens.values():
        if s and hasattr(s, 'shutdown'):
            s.shutdown()
    app.destroy()
    monkeypatch.undo()
    return all(visible)


if __name__ == '__main__':
    ok = True
    for scale in (1.0, 1.25, 1.5, 2.0):
        try:
            ok &= run_case(scale)
        except Exception as exc:
            print(f"scale {scale}: exception {exc!r}")
            ok = False
    print('ALL OK' if ok else 'FAILURES PRESENT')
