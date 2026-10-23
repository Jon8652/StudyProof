"""Native Tk integration checks; skips cleanly without a display."""
import threading
import time
import tkinter

import pytest

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


def test_unconfigured_coach_opens_and_only_prompts_on_interaction(monkeypatch):
    from types import SimpleNamespace
    from app.core.models import AISettings
    from app.core.agent.schema import CoachReply
    from app.core.result import ActionResult
    from app.gui.screens import coach_screen

    state = AppState()
    monkeypatch.setattr(state, '_mark_dirty_and_save', lambda: None)
    note = state.create_note('Biology', 'Sunlight supplies energy to plants.').value
    # A saved conversation must remain readable after credentials are cleared.
    from app.core.agent.coach_service import CoachService
    service = CoachService(state)
    state.settings.ai = AISettings('live', 'https://example.test/v1', api_key='test-key')
    session = service.start_session([('note', note.id)]).value
    job = service.prepare_turn(session.id, 'question').value
    assert service.complete_turn(job, ActionResult(True, value=CoachReply(
        'question', 'How does sunlight help plants?'))).ok
    state.settings.ai = AISettings()
    notifications = []
    monkeypatch.setattr(coach_screen, 'show_message', lambda *args: notifications.append(args))
    try:
        app = MainWindow(app_state=state)
    except tkinter.TclError as exc:
        pytest.skip(f'Tk display unavailable: {exc}')
    try:
        app.withdraw()
        app._nav_buttons['coach'].invoke()
        screen = app.screens['coach']
        screen.resume_session(session.id)
        screen.refresh()
        assert app.current_screen_key == 'coach'
        assert screen.thread.winfo_children()
        assert not notifications
        assert screen.send_button.cget('state') == 'normal'
        assert screen.composer.cget('state') == 'normal'
        assert screen._composer_input(SimpleNamespace(char='')) is None
        assert not notifications
        assert screen._composer_input(SimpleNamespace(char='a')) == 'break'
        assert screen._composer_paste() == 'break'
        screen.send_button.invoke()
        screen.buttons['hint'].invoke()
        assert len(notifications) == 4
        assert len(session.turns) == 1
    finally:
        for screen in app.screens.values():
            if hasattr(screen, 'shutdown'):
                screen.shutdown()
        app.destroy()


def _message_labels(bubble):
    return [child for child in bubble._wrapped_labels]


def test_coach_text_width_tracks_the_bubble_at_every_window_size(monkeypatch):
    """Maximised windows must not strand the text at a fixed cap.

    Regression: wraplength was clamped to 900px, so on a wide screen the
    coach column stayed narrow with dead space right of the bubble card.
    The wraplength must instead follow each widget's own allocated width
    (floor only), and shrink again when the window is restored.
    """
    from app.core.models import CoachTurn
    from app.gui.widgets.chat_bubble import MIN_WRAPLENGTH, target_wraplength

    state = AppState()
    monkeypatch.setattr(state, '_mark_dirty_and_save', lambda: None)
    note = state.create_note('Biology', 'Chlorophyll absorbs sunlight for photosynthesis.').value
    state.set_ai_settings({'base_url': 'https://example.test/v1', 'api_key': 'test-key'})
    session = state.create_coach_session([state.source_ref_for('note', note.id)]).value
    long_text = ('Photosynthesis converts light energy into chemical energy. ' * 40).strip()
    session.turns.append(CoachTurn.create('coach', 'question', long_text))
    try:
        app = MainWindow(app_state=state)
    except tkinter.TclError as exc:
        pytest.skip(f'Tk display unavailable: {exc}')
    try:
        app.withdraw()
        app.geometry('1600x900')
        app.show_screen('coach')
        screen = app.screens['coach']
        screen.resume_session(session.id)
        pump(app, lambda: bool(screen.thread.winfo_children()))
        bubbles = list(screen.thread.winfo_children())
        assert bubbles
        # Pump until the Configure-driven wraplength settles at the wide size.
        def wrapped_wide():
            app.update_idletasks()
            app.update()
            widths = [label.cget('wraplength') for b in bubbles for label in _message_labels(b)]
            return len(widths) == 2 and all(w > 900 for w in widths)
        deadline = time.monotonic() + 5
        while not wrapped_wide() and time.monotonic() < deadline:
            time.sleep(.02)
        assert wrapped_wide(), 'wide-window wraplength never exceeded the old 900px ceiling'
        for b in bubbles:
            for label in _message_labels(b):
                # Text may exceed the old cap but never the bubble's own row.
                assert label.cget('wraplength') <= max(label.winfo_width(), 1) + 2 or \
                    label.cget('wraplength') <= target_wraplength(
                        b.winfo_width(), float(b._get_widget_scaling()))
        # Restoring to a small window must narrow the text again promptly.
        app.geometry('700x600')
        def wrapped_narrow():
            app.update_idletasks()
            app.update()
            widths = [label.cget('wraplength') for b in bubbles for label in _message_labels(b)]
            return len(widths) == 2 and all(w < 900 for w in widths)
        deadline = time.monotonic() + 5
        while not wrapped_narrow() and time.monotonic() < deadline:
            time.sleep(.02)
        assert wrapped_narrow(), 'shrink events did not re-wrap the coach text'
        # Header/notice/confidence rows track their own allocated width too.
        header_target = target_wraplength(max(screen.header.winfo_width(), 1),
                                          float(screen._get_widget_scaling()))
        assert screen.header.cget('wraplength') == max(MIN_WRAPLENGTH, header_target)
    finally:
        for screen in app.screens.values():
            if hasattr(screen, 'shutdown'):
                screen.shutdown()
        app.destroy()


def test_full_live_loop_and_cancel(monkeypatch, scripted_provider):
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
        state.set_ai_settings({'base_url': 'https://example.test/v1', 'api_key': 'test-key'})
        class ReviewProvider(scripted_provider):
            confidence = 'low'
        screen.service.live_factory = ReviewProvider
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
        pump(app, lambda: not screen.service.assessments)
        assert state.get_review_metrics().weak_topics
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
                return scripted_provider(None).generate(context)
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


def test_revise_weak_topics_picker_and_hidden_background_score(monkeypatch, scripted_provider):
    from app.core.agent.schema import CoachReply
    state = AppState()
    monkeypatch.setattr(state, '_mark_dirty_and_save', lambda: None)
    note = state.create_note('Biology', 'Chlorophyll absorbs sunlight for photosynthesis.').value
    state.set_ai_settings({'base_url': 'https://example.test/v1', 'api_key': 'test-key'})
    class RevisionProvider(scripted_provider):
        confidence = 'low'
        def generate(self, context):
            return CoachReply(context['kind'], 'How does chlorophyll help photosynthesis?'
                              if context['kind'] == 'question' else 'Think about light energy.')
    try:
        app = MainWindow(app_state=state)
    except tkinter.TclError as exc:
        pytest.skip(f'Tk display unavailable: {exc}')
    try:
        app.withdraw()
        app.show_screen('coach')
        screen = app.screens['coach']
        screen.service.live_factory = RevisionProvider
        session = screen.service.start_session([('note', note.id)]).value
        screen.session_id = session.id
        screen.request('question')
        pump(app, lambda: not screen.busy)
        screen.composer.insert('1.0', 'It eats sunlight.')
        screen.send()
        pump(app, lambda: not screen.busy and not screen.service.assessments)
        weak = state.dataset.weak_topics[0]
        # Select through the actual dialog while avoiding an interactive wait.
        monkeypatch.setattr(screen, 'wait_window', lambda dialog: dialog.choose(weak.id))
        screen.revise_weak_topics()
        revision = state.coach_session_by_id(screen.session_id)
        assert revision.id != session.id
        assert weak.question in revision.turns[0].text
        assert screen.confidence_note.cget('text') == ''
        RevisionProvider.confidence = 'high'
        screen.composer.insert('1.0', 'Chlorophyll absorbs the light needed for photosynthesis.')
        screen.send()
        pump(app, lambda: not screen.busy and not screen.service.assessments)
        assert not state.dataset.weak_topics
        app.show_screen('stats')
        stats = app.screens['stats']
        assert not state.get_review_metrics().weak_topics
        assert stats._weak_empty.winfo_exists()
        assert all(t.confidence is None for t in revision.turns)
        assert 'confidence rating' not in screen.confidence_note.cget('text').lower()
    finally:
        for screen in app.screens.values():
            if hasattr(screen, 'shutdown'):
                screen.shutdown()
        app.destroy()


def test_statistics_uses_coach_topics_and_resumes_exact_revision(monkeypatch):
    import customtkinter as ctk
    from app.core.models import QuestionRecord, WeakTopic

    state = AppState()
    monkeypatch.setattr(state, '_mark_dirty_and_save', lambda: None)
    state.set_ai_settings({'base_url': 'https://example.test/v1', 'api_key': 'test-key'})
    note = state.create_note('AI notes', 'Transformers use self-attention.').value
    source = state.source_ref_for('note', note.id)
    session = state.create_coach_session([source]).value
    question = QuestionRecord.create('How do transformers work?')
    question.first_answer, question.confidence, question.hints_seen = 'I am unsure.', 'low', 3
    session.questions.append(question)
    try:
        app = MainWindow(app_state=state)
    except tkinter.TclError as exc:
        pytest.skip(f'Tk display unavailable: {exc}')
    try:
        app.withdraw()
        app.show_screen('stats')
        stats = app.screens['stats']
        # Legacy confidence and hint totals never flag extra topics.
        assert state.get_review_metrics().weak_topics == []
        assert stats._weak_empty.winfo_exists()
        assert not hasattr(stats, '_confidence_summary')

        transformer = WeakTopic('transformer', source, 'Transformers',
                                question.prompt, session.id, question.id, 20)
        attention = WeakTopic('attention', source, 'Self-attention',
                              'What does self-attention do?', session.id, question.id, 30)
        state.dataset.weak_topics = [transformer, attention]
        stats.refresh()
        rows = [child for child in stats._weak_container.winfo_children()
                if child is not stats._weak_header_row]
        assert len(rows) == 2
        for row, topic in zip(rows, state.get_review_metrics().weak_topics):
            labels = [child for child in row.winfo_children() if isinstance(child, ctk.CTkLabel)]
            assert [label.cget('text') for label in labels] == ['AI notes', topic.topic]
            assert [int(label.grid_info()['column']) for label in labels] == [0, 1]
        target = state.get_review_metrics().weak_topics[0]
        # Resume starts this exact topic's revision even without the original chat.
        assert state.delete_coach_session(session.id).ok
        rows = [child for child in stats._weak_container.winfo_children()
                if child is not stats._weak_header_row]
        button = next(child for child in rows[0].winfo_children() if isinstance(child, ctk.CTkButton))
        assert int(button.grid_info()['column']) == 2
        button.invoke()
        coach = app.screens['coach']
        revision = state.coach_session_by_id(coach.session_id)
        assert app.current_screen_key == 'coach'
        assert revision.questions[0].prompt == target.question
        assert target.topic in revision.turns[0].text

        state.delete_note(note.id)
        app.show_screen('stats')
        rows = [child for child in stats._weak_container.winfo_children()
                if child is not stats._weak_header_row]
        assert len(rows) == 2
        for row in rows:
            button = next(child for child in row.winfo_children() if isinstance(child, ctk.CTkButton))
            assert button.cget('state') == 'disabled'
    finally:
        for screen in app.screens.values():
            if hasattr(screen, 'shutdown'):
                screen.shutdown()
        app.destroy()


def test_prompt_terminology_calls_the_material_notes():
    """Coach prompts must never name the material a 'source pack'.

    The learner-facing JSON payload key stays ``source_pack`` (data
    contract), but every instruction sentence must say "notes" so the
    model never echoes internal terminology in its replies.
    """
    from app.core.agent import prompt

    for name in ('SYSTEM_PROMPT', 'CONVERSATION_PROMPT'):
        text = getattr(prompt, name)
        assert 'source pack' not in text.replace(
            'never use the terms "source pack"', '')          # only as forbidden term
        assert 'the pack' not in text
        assert 'notes' in text
