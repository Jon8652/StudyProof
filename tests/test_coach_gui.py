"""Native Tk integration checks; skips cleanly without a display."""
import threading
import time
import tkinter

import pytest

from app.core.agent.mock_engine import MockProvider
from app.gui.main_window import MainWindow, NAV_ITEMS
from app.state.app_state import AppState
from app.persistence.repository import dataset_to_payload
from app.gui.dialogs.source_select import SourceSelectDialog
from app.gui.dialogs.sessions_dialog import SessionsDialog


def pump(app, predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.update()
        time.sleep(.01)
    assert predicate()


def test_full_mock_loop_and_live_cancel(monkeypatch):
    state = AppState()
    monkeypatch.setattr(state, '_mark_dirty_and_save', lambda: None)
    note = state.create_note('Biology',
        'Photosynthesis converts sunlight into chemical energy stored in glucose, '
        'while chlorophyll absorbs the light needed for this process.').value
    try:
        app = MainWindow(app_state=state)
    except tkinter.TclError as exc:
        pytest.skip(f'Tk display unavailable: {exc}')
    release = threading.Event()
    try:
        app.withdraw()
        assert [n[0] for n in NAV_ITEMS] == ['home', 'calendar', 'notes', 'library', 'coach', 'stats', 'settings']
        app.show_screen('coach')
        screen = app.screens['coach']
        session = screen.service.start_session([('note', note.id)]).value
        screen.session_id = session.id
        screen.request('question')
        pump(app, lambda: not screen.busy)
        assert len(session.questions) == 1
        assert screen._suggestion_job is not None
        screen._hide_suggestions()
        screen.use_suggestion('In my own words, ')
        assert screen.composer.get('1.0', 'end').strip() == 'In my own words,'
        assert session.questions[0].first_answer is None
        screen.composer.delete('1.0', 'end')
        screen.composer.insert('1.0', 'Energy from sunlight')
        assert 'answer' not in screen.buttons and 'feedback' not in screen.buttons
        assert 'revision' not in screen.buttons
        assert screen.send() == 'break'
        pump(app, lambda: not screen.busy)
        screen.composer.insert('1.0', 'Chlorophyll captures sunlight to make glucose.')
        screen.send()
        pump(app, lambda: not screen.busy)
        assert session.questions[0].revisions
        assert state.get_review_metrics().weak_sources
        screen.request('followup')
        pump(app, lambda: not screen.busy)
        assert session.questions[-1].parent_id == session.questions[0].id
        app.show_screen('stats')
        app.screens['stats'].refresh()
        app.show_screen('settings')
        settings = app.screens['settings']
        # Change request #4: the model is a dropdown of exact names now.
        assert set(settings.ai_model_menu.cget('values')) == {
            'deepseek-flash', 'deepseek-v4-pro'}
        settings.ai_model_menu.set('deepseek-v4-pro')
        # CTkOptionMenu invokes its command with the chosen value; saving
        # must tolerate being called both as a callback and bare.
        settings._save_ai('deepseek-v4-pro')
        assert state.settings.ai.model == 'deepseek-v4-pro'
        app.show_screen('coach')
        screen.composer.insert('1.0', 'Energy')
        screen.answer()
        state.set_ai_settings({'mode': 'live', 'base_url': 'https://example.test/v1', 'api_key': 'test-key'})
        started = threading.Event()
        class SlowProvider:
            def __init__(self, settings): pass
            def generate(self, context):
                started.set()
                release.wait(3)
                return MockProvider().generate(context)
        screen.service.live_factory = SlowProvider
        before = len(session.turns)
        screen.request('feedback')
        pump(app, started.is_set)
        begin = time.monotonic()
        screen.cancel()
        app.update()
        assert time.monotonic() - begin < 1
        assert screen.composer.cget('state') == 'normal'
        assert not screen.busy
        release.set()
        for _ in range(10):
            app.update()
            time.sleep(.01)
        assert len(session.turns) == before
        payload = dataset_to_payload(state.dataset)
        assert payload['coach_sessions'][0]['questions'][0]['first_answer'] == 'Energy from sunlight'
        picker = SourceSelectDialog(app, state)
        assert callable(picker.state)  # preserve Tk's window-state method on Windows
        picker._ordered_ids = [('note', note.id)]
        picker._confirm()
        assert picker.result == [('note', note.id)]
        sessions = SessionsDialog(app, state, screen.service)
        assert callable(sessions.state)
        sessions.resume(session.id)
        assert sessions.result == session.id
    finally:
        release.set()
        for s in app.screens.values():
            if hasattr(s, 'shutdown'): s.shutdown()
        app.destroy()
