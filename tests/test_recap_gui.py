"""Native Coach recap flow, including cancellation and exact-question resume."""
import threading
import time
import tkinter

import pytest

from app.core.models import CoachTurn, QuestionRecord
from app.core.agent.provider_base import ProviderError
from app.gui.main_window import MainWindow
from app.state.app_state import AppState


def pump(app, predicate, timeout=4):
    end = time.monotonic() + timeout
    while not predicate() and time.monotonic() < end:
        app.update()
        time.sleep(.01)
    assert predicate()


@pytest.fixture
def recap_app(monkeypatch):
    state = AppState()
    monkeypatch.setattr(state, '_mark_dirty_and_save', lambda: None)
    note = state.create_note('Biology', 'Plants use sunlight.').value
    session = state.create_coach_session([state.source_ref_for('note', note.id)]).value
    question = QuestionRecord.create('How do plants use sunlight?')
    session.questions.append(question)
    session.turns.append(CoachTurn.create('coach', 'question', question.prompt, question.id))
    try:
        app = MainWindow(app_state=state)
    except tkinter.TclError as exc:
        pytest.skip(f'Tk display unavailable: {exc}')
    app.withdraw()
    app.show_screen('coach')
    screen = app.screens['coach']
    screen.resume_session(session.id)
    yield app, screen, state, session, question
    for item in app.screens.values():
        if hasattr(item, 'shutdown'):
            item.shutdown()
    app.destroy()


def test_recap_offline_offer_yes_no_reopen_and_repeat(recap_app):
    app, screen, state, session, question = recap_app
    screen.recap_button.invoke()
    assert screen.busy and screen.recap_button.cget('state') == 'disabled'
    assert screen.composer.cget('state') == 'disabled'
    assert screen._recap_loading_bubble is not None
    pump(app, lambda: not screen.busy)
    weekly = session.turns[-1]
    assert weekly.kind == 'recap' and weekly.recap['offer']
    bubble = screen._rendered_turns[-1][1]
    assert any('Resume:' in b.cget('text') for b in bubble.recap_buttons)
    assert all(str(b._canvas.cget('takefocus')) == '1' for b in bubble.recap_buttons)
    next(b for b in bubble.recap_buttons if b.cget('text').startswith('Yes')).invoke()
    pump(app, lambda: not screen.busy)
    assert session.turns[-1].recap['days'] == 30 and not session.turns[-1].recap['offer']
    assert not weekly.recap['offer']
    screen.request_recap()
    pump(app, lambda: not screen.busy)
    weekly = session.turns[-1]
    bubble = screen._rendered_turns[-1][1]
    next(b for b in bubble.recap_buttons if b.cget('text').startswith('No')).invoke()
    assert not weekly.recap['offer'] and len(session.turns) == 4
    screen.resume_session(session.id)
    assert len(screen._rendered_turns) == 4


def test_cancel_discards_late_result_and_restores_composer(recap_app):
    app, screen, state, session, question = recap_app
    entered, release = threading.Event(), threading.Event()
    generate = screen.service.generate_recap
    def slow(job):
        entered.set()
        release.wait(3)
        return generate(job)
    screen.service.generate_recap = slow
    try:
        screen.request_recap()
        pump(app, entered.is_set)
        started = time.monotonic()
        screen.cancel()
        assert time.monotonic() - started < 1
        assert not screen.busy and screen.composer.cget('state') == 'normal'
        assert screen._recap_loading_bubble is None
        release.set()
        for _ in range(10):
            app.update()
            time.sleep(.01)
        assert len(session.turns) == 1
    finally:
        release.set()


def test_resume_scrolls_to_exact_question_answers_it_and_handles_deleted_session(recap_app):
    app, screen, state, session, question = recap_app
    app.geometry('1100x800')
    app.deiconify()
    session.turns[:0] = [CoachTurn.create('coach', 'notice', 'Earlier content ' * 40) for _ in range(8)]
    other = QuestionRecord.create('A later unanswered question?')
    session.questions.append(other)
    for _ in range(35):
        session.turns.append(CoachTurn.create('coach', 'notice', 'Later content ' * 40))
    session.turns.append(CoachTurn.create('coach', 'question', other.prompt, other.id))
    screen.refresh()
    screen.request_recap()
    pump(app, lambda: not screen.busy)
    link = next(item for item in session.turns[-1].recap['loose_ends'] if item['question_id'] == question.id)
    screen.resume_recap_question(link)
    for _ in range(10):
        app.update()
        time.sleep(.01)
    assert screen.session_id == session.id and screen._resume_question_id == question.id
    assert not screen._stick_to_bottom
    question_bubble = screen._rendered_turns[8][1]
    top = screen._viewport_top()
    assert top <= question_bubble.winfo_y() + 20
    assert question_bubble.winfo_y() < top + screen.thread._parent_canvas.winfo_height()
    result = screen.service.submit_message(session.id, 'They use sunlight for energy.', screen._resume_question_id)
    assert result.ok and question.first_answer is not None and other.first_answer is None
    # The source may disappear without preventing navigation to the saved question.
    state.delete_note(session.sources[0].id)
    screen.resume_recap_question(link)
    assert screen.session_id == session.id
    state.delete_coach_session(session.id)
    screen.resume_recap_question(link)
    assert screen.notice.cget('text') == 'That session was deleted.'


def test_provider_failure_falls_back_and_empty_preview_creates_no_session(recap_app):
    app, screen, state, session, question = recap_app
    state.settings.ai.api_key = 'fake-key'
    class Offline:
        def __init__(self, settings):
            pass
        def generate(self, context):
            raise ProviderError('Offline')
    screen.service.live_factory = Offline
    screen.request_recap()
    pump(app, lambda: not screen.busy)
    assert session.turns[-1].recap['simplified']
    assert screen._rendered_turns[-1][1].recap_buttons
    screen.resume_session(None)
    count = len(state.dataset.coach_sessions)
    screen.request_recap()
    pump(app, lambda: not screen.busy)
    assert len(state.dataset.coach_sessions) == count
    assert screen._recap_preview.turns[-1].kind == 'recap'
