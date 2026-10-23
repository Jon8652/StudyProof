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
        # NOT withdraw(): a withdrawn window never receives real geometry
        # updates from Tk, so every widget stays at its 1px un-mapped size
        # and no <Configure> events fire — the wraplength would sit at its
        # floor forever.  Map the window far outside the virtual screen so
        # Tk treats it as visible (geometry events flow) while nothing is
        # actually shown on the display.
        app.geometry('+3000+3000')
        app.deiconify()
        app.geometry('1600x900')
        app.show_screen('coach')
        screen = app.screens['coach']
        screen.resume_session(session.id)
        pump(app, lambda: bool(screen.thread.winfo_children()))
        bubbles = list(screen.thread.winfo_children())
        assert bubbles
        # The fixture has one coach turn carrying no quotations or
        # verification notices, so each bubble holds exactly one wrapped
        # label; derive the count from the widgets rather than hard-coding
        # an assumption that silently breaks when the fixture changes.
        expected_labels = sum(len(_message_labels(b)) for b in bubbles)
        # Pump until the Configure-driven wraplength settles at the wide size.
        def wrapped_wide():
            app.update_idletasks()
            app.update()
            widths = [label.cget('wraplength') for b in bubbles for label in _message_labels(b)]
            return len(widths) == expected_labels and all(w > 900 for w in widths)
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
            return len(widths) == expected_labels and all(w < 900 for w in widths)
        deadline = time.monotonic() + 5
        while not wrapped_narrow() and time.monotonic() < deadline:
            time.sleep(.02)
        assert wrapped_narrow(), 'shrink events did not re-wrap the coach text'
        # Header/notice/confidence rows track their own allocated width too.
        # Their updates are debounced behind an after(50) job, so pump until
        # the settled value matches the geometry-derived target instead of
        # asserting against a mid-flight snapshot.
        def header_settled():
            app.update_idletasks()
            app.update()
            header_target = target_wraplength(max(screen.header.winfo_width(), 1),
                                              float(screen._get_widget_scaling()))
            return screen.header.cget('wraplength') == max(MIN_WRAPLENGTH, header_target)
        deadline = time.monotonic() + 5
        while not header_settled() and time.monotonic() < deadline:
            time.sleep(.02)
        assert header_settled(), 'header wraplength never tracked its allocated row width'
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


# ---------------------------------------------------------------------------
# Auto-scroll ("stick to bottom") behaviour


def _seed_long_session(state, note, turns=25):
    """Create a session whose thread is far taller than any test viewport."""
    from app.core.models import CoachTurn
    long_text = ('Photosynthesis converts light energy into chemical energy. ' * 12).strip()
    session = state.create_coach_session([state.source_ref_for('note', note.id)]).value
    for i in range(turns):
        role = 'coach' if i % 2 == 0 else 'user'
        kind = 'question' if role == 'coach' else 'answer'
        session.turns.append(CoachTurn.create(role, kind, f'{i}. {long_text}'))
    return session


def _settle(app, screen, timeout=3):
    """Pump until the settle poll has run its course and stopped itself."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.update()
        time.sleep(.02)
        if screen._settle_job is None and screen._pin_job is None:
            # One more quiet cycle to be sure nothing rescheduled itself.
            app.update()
            time.sleep(.08)
            if screen._settle_job is None and screen._pin_job is None:
                return True
    return False


class GatedProvider:
    """Scripted reply generator that blocks until a test releases it.

    Lets a test hold a coach turn "in flight" deterministically — no race
    against thread scheduling — so behaviour *during* generation can be
    asserted (e.g. manual scrolling must survive the eventual reply).
    ``prepare_turn`` probes the provider synchronously on the main thread,
    so only the worker-side generations (kinds listed in ``gate_kinds``)
    block on the gate; the rest answer immediately.
    """
    confidence = 'high'
    release = threading.Event()
    started = threading.Event()
    gate_kinds = ()

    def __init__(self, settings):
        pass

    def generate(self, context):
        from app.core.agent.schema import CoachReply
        if context['kind'] in self.gate_kinds:
            self.started.set()
            assert self.release.wait(5), 'provider was never released'
        return CoachReply(context['kind'],
                          'Confidence: high. Explain how sunlight supplies energy.')

    def assess_answer(self, context):
        from app.core.agent.schema import AnswerAssessment
        span = context['pack'].spans[0]
        return AnswerAssessment(span.source_id, 'Light and energy',
                                context['question'].prompt, 95)


def test_thread_pins_to_bottom_when_content_overflows(monkeypatch):
    """Long sessions must land with the newest bubble fully visible."""
    state = AppState()
    monkeypatch.setattr(state, '_mark_dirty_and_save', lambda: None)
    note = state.create_note('Biology', 'Chlorophyll absorbs sunlight for photosynthesis.').value
    state.set_ai_settings({'base_url': 'https://example.test/v1', 'api_key': 'test-key'})
    session = _seed_long_session(state, note)
    try:
        app = MainWindow(app_state=state)
    except tkinter.TclError as exc:
        pytest.skip(f'Tk display unavailable: {exc}')
    try:
        app.geometry('+3000+3000')
        app.deiconify()
        app.geometry('700x400')
        app.show_screen('coach')
        screen = app.screens['coach']
        canvas = screen._thread_canvas
        assert canvas is not None, 'CTkScrollableFrame lost _parent_canvas'
        screen.resume_session(session.id)
        pump(app, lambda: len(screen.thread.winfo_children()) >= len(session.turns))
        assert _settle(app, screen), 'settle poll never terminated'
        assert screen._thread_at_bottom()
        # The scrollregion must extend well past the viewport (content really
        # overflows) and the last bubble's bottom edge must sit inside it.
        region = canvas.bbox('all')
        assert region[3] > canvas.winfo_height() + 100
        last = screen.thread.winfo_children()[-1]
        top = canvas.canvasy(0)
        assert last.winfo_y() + last.winfo_height() - top <= canvas.winfo_height() + 6, \
            'newest bubble is clipped at the bottom of the viewport'
    finally:
        for s in app.screens.values():
            if hasattr(s, 'shutdown'):
                s.shutdown()
        app.destroy()


def test_sending_a_message_resticks_the_view_to_the_bottom(monkeypatch, scripted_provider):
    state = AppState()
    monkeypatch.setattr(state, '_mark_dirty_and_save', lambda: None)
    note = state.create_note('Biology',
        'Photosynthesis converts sunlight into chemical energy stored in glucose, '
        'while chlorophyll absorbs the light needed for this process.').value
    try:
        app = MainWindow(app_state=state)
    except tkinter.TclError as exc:
        pytest.skip(f'Tk display unavailable: {exc}')
    try:
        # Mapped (never withdrawn): a withdrawn window receives no geometry
        # events, so every widget stays un-mapped at 1px and neither the pin
        # nor _thread_at_bottom() can ever judge real positions.
        app.geometry('+3000+3000')
        app.deiconify()
        app.geometry('700x400')
        app.show_screen('coach')
        screen = app.screens['coach']
        state.set_ai_settings({'base_url': 'https://example.test/v1', 'api_key': 'test-key'})
        screen.service.live_factory = scripted_provider
        session = screen.service.start_session([('note', note.id)]).value
        screen.session_id = session.id
        screen.request('question')
        pump(app, lambda: not screen.busy)
        assert _settle(app, screen)
        assert screen._thread_at_bottom()
        # Fill the thread until it definitely overflows the small viewport.
        from app.core.models import CoachTurn
        long_text = ('Energy explanation detail. ' * 30).strip()
        for i in range(20):
            session.turns.append(CoachTurn.create(
                'coach' if i % 2 else 'user', 'notice', f'{i}. {long_text}'))
        screen.refresh()
        pump(app, lambda: len(screen.thread.winfo_children()) >= 21)
        assert _settle(app, screen)
        # Park mid-scroll with a genuine upward user gesture — the pin
        # releases and Tk leaves the view exactly where the user put it.
        screen._thread_canvas.yview_scroll(-5000, 'units')
        assert not screen._stick_to_bottom
        parked = screen._thread_canvas.yview()[0]
        # Now send: the user's message is brand-new content, which must
        # re-arm auto-scroll even though the user had scrolled away first.
        screen.composer.insert('1.0', 'Energy from sunlight')
        screen.send()
        pump(app, lambda: not screen.busy)
        assert screen._stick_to_bottom, 'send/reply must restick to the bottom'
        assert _settle(app, screen)
        assert screen._thread_at_bottom()
        assert screen._thread_canvas.yview()[0] > parked, \
            'the reply must actually pull the view back down'
    finally:
        for s in app.screens.values():
            if hasattr(s, 'shutdown'):
                s.shutdown()
        app.destroy()


def test_session_switch_lands_at_bottom_in_both_directions(monkeypatch):
    """Switching sessions pins absolutely: works when the bottom is above or below."""
    state = AppState()
    monkeypatch.setattr(state, '_mark_dirty_and_save', lambda: None)
    note = state.create_note('Biology', 'Chlorophyll absorbs sunlight for photosynthesis.').value
    state.set_ai_settings({'base_url': 'https://example.test/v1', 'api_key': 'test-key'})
    long_session = _seed_long_session(state, note, turns=25)
    short_session = _seed_long_session(state, note, turns=2)
    try:
        app = MainWindow(app_state=state)
    except tkinter.TclError as exc:
        pytest.skip(f'Tk display unavailable: {exc}')
    try:
        app.geometry('+3000+3000')
        app.deiconify()
        app.geometry('700x400')
        app.show_screen('coach')
        screen = app.screens['coach']
        screen.resume_session(long_session.id)
        pump(app, lambda: len(screen.thread.winfo_children()) >= 25)
        assert _settle(app, screen)
        assert screen._thread_at_bottom()
        # Park mid-scroll in the long session, then switch to the SHORT one:
        # landing at its bottom means scrolling *up* relative to the old view.
        screen._thread_canvas.yview_moveto(0.4)
        screen.resume_session(short_session.id)
        pump(app, lambda: len(screen.thread.winfo_children()) == 2)
        assert _settle(app, screen)
        assert screen._thread_at_bottom(), 'short-session switch must pin (scroll up)'
        # And back to the long session: now the bottom is far *below*.
        screen.resume_session(long_session.id)
        pump(app, lambda: len(screen.thread.winfo_children()) == 25)
        assert _settle(app, screen)
        assert screen._thread_at_bottom(), 'long-session switch must pin (scroll down)'
    finally:
        for s in app.screens.values():
            if hasattr(s, 'shutdown'):
                s.shutdown()
        app.destroy()


def test_scrolling_up_releases_the_pin(monkeypatch):
    state = AppState()
    monkeypatch.setattr(state, '_mark_dirty_and_save', lambda: None)
    note = state.create_note('Biology', 'Chlorophyll absorbs sunlight for photosynthesis.').value
    state.set_ai_settings({'base_url': 'https://example.test/v1', 'api_key': 'test-key'})
    session = _seed_long_session(state, note, turns=25)
    try:
        app = MainWindow(app_state=state)
    except tkinter.TclError as exc:
        pytest.skip(f'Tk display unavailable: {exc}')
    try:
        app.geometry('+3000+3000')
        app.deiconify()
        app.geometry('700x400')
        app.show_screen('coach')
        screen = app.screens['coach']
        screen.resume_session(session.id)
        pump(app, lambda: len(screen.thread.winfo_children()) >= 25)
        assert _settle(app, screen)
        # User scrolls up to read history (the yview wrapper sees the upward
        # request and hands control over): the pin releases and Tk leaves
        # them exactly where they put themselves.
        canvas = screen._thread_canvas
        canvas.yview_moveto(0.3)
        assert not screen._stick_to_bottom
        before = canvas.yview()[0]
        for _ in range(10):
            app.update()
            time.sleep(.02)
        assert screen._thread_at_bottom() is False
        assert abs(canvas.yview()[0] - before) < 0.02, \
            'view was yanked away after the user took over'
        # Wheel motion over the thread releases the pin too, even when the
        # gesture happens while auto-scroll jobs are still pending — but a
        # genuine content change must arm them first.  A plain refresh() of
        # identical content deliberately no longer does (button clicks that
        # only re-render must never yank the viewport).
        from app.core.models import CoachTurn
        session.turns.append(CoachTurn.create('user', 'answer', 'Fresh content.'))
        screen.refresh()
        assert screen._stick_to_bottom
        assert screen._settle_job is not None or screen._pin_job is not None
        screen._on_thread_wheel_event(None)  # wheel observer fires first
        canvas.yview_scroll(-100, 'units')   # ... then the actual scroll-up
        assert not screen._stick_to_bottom
        assert screen._settle_job is None and screen._pin_job is None, \
            'pending auto jobs must be cancelled when the user takes over'
        pos_after_release = canvas.yview()[0]
        for _ in range(10):
            app.update()
            time.sleep(.02)
        assert abs(canvas.yview()[0] - pos_after_release) < 0.02, \
            'a stale settle/pin pass dragged the view back down'
        # Scrolling all the way back to the bottom re-engages stickiness.
        canvas.yview_scroll(10_000_000, 'units')   # clamps at the real bottom
        app.update()
        assert screen._stick_to_bottom, 'manual return to bottom must re-stick'
        # And a mid-page downward click on the scrollbar track must NOT.
        screen._stick_to_bottom = False
        canvas.yview_moveto(0.3)
        app.update()
        canvas.yview_scroll(50, 'units')     # still far from the bottom
        app.update()
        assert not screen._stick_to_bottom, \
            'downward scrolling must only re-stick once actually at the bottom'
    finally:
        for s in app.screens.values():
            if hasattr(s, 'shutdown'):
                s.shutdown()
        app.destroy()


def test_manual_scroll_survives_a_coach_reply(monkeypatch):
    """Auto-scroll may only follow NEW content — never fight a live manual scroll.

    The wheel-up handler runs *before* CTk's own bind_all wheel handler
    scrolls the canvas; by the time the deferred reply rebuild reaches its
    idle pin, the user's newer gesture must veto it entirely.  A gated
    provider holds the turn in flight deterministically so the scroll-up
    provably happens *during* generation (no race against thread timing).
    """
    state = AppState()
    monkeypatch.setattr(state, '_mark_dirty_and_save', lambda: None)
    note = state.create_note('Biology',
        'Photosynthesis converts sunlight into chemical energy stored in glucose, '
        'while chlorophyll absorbs the light needed for this process.').value
    try:
        app = MainWindow(app_state=state)
    except tkinter.TclError as exc:
        pytest.skip(f'Tk display unavailable: {exc}')
    GatedProvider.release.clear()
    GatedProvider.started.clear()
    try:
        app.geometry('+3000+3000')
        app.deiconify()
        app.geometry('700x400')
        app.show_screen('coach')
        screen = app.screens['coach']
        canvas = screen._thread_canvas
        state.set_ai_settings({'base_url': 'https://example.test/v1', 'api_key': 'test-key'})
        screen.service.live_factory = GatedProvider
        session = screen.service.start_session([('note', note.id)]).value
        screen.session_id = session.id
        # First turn must complete (release gate) before the second starts.
        screen.request('question')
        pump(app, GatedProvider.started.is_set)
        GatedProvider.release.set()
        pump(app, lambda: not screen.busy)
        assert _settle(app, screen)
        from app.core.models import CoachTurn
        long_text = ('Energy explanation detail. ' * 30).strip()
        for i in range(20):
            session.turns.append(CoachTurn.create(
                'coach' if i % 2 else 'user', 'notice', f'{i}. {long_text}'))
        screen.refresh()
        pump(app, lambda: len(screen.thread.winfo_children()) >= 21)
        assert _settle(app, screen)
        assert screen._thread_at_bottom()
        # Start a turn and keep it in flight behind the gate...
        GatedProvider.release.clear()
        GatedProvider.started.clear()
        screen.request('question')
        pump(app, GatedProvider.started.is_set)
        assert screen.busy
        # ...then scroll up to read history WHILE the coach is still thinking.
        screen._on_thread_wheel_event(None)      # wheel observer...
        canvas.yview_scroll(-400, 'units')       # ...then CTk scrolls up
        parked = canvas.yview()[0]
        assert parked < 0.99
        # Release the reply: its rebuild arms a fresh window, and stickiness
        # re-engages for FUTURE changes — but the user's newer gesture vetoes
        # every pending pin, so the viewport itself must not move.
        GatedProvider.release.set()
        pump(app, lambda: not screen.busy)       # reply lands, refresh re-arms
        for _ in range(10):                      # let any stray pin fire
            app.update()
            time.sleep(.02)
        assert screen._stick_to_bottom, 'a fresh reply resticks for the NEXT change'
        assert abs(canvas.yview()[0] - parked) < 0.02, \
            'the reply rebuild yanked the view away from the user mid-flight'
    finally:
        GatedProvider.release.set()
        for s in app.screens.values():
            if hasattr(s, 'shutdown'):
                s.shutdown()
        app.destroy()


def test_repeated_refresh_of_identical_content_never_moves_the_view(monkeypatch):
    """Regression: button clicks must not trigger auto-scroll.

    "Sessions…" closing without switching and the weak-topics picker churn
    used to end in refresh() calls that re-rendered an *identical* thread —
    each one armed a fresh pin window and yanked the viewport back down
    mid-manual-scroll.  Auto-scroll may only follow new conversation content
    (a sent message or a coach reply), so a same-content rebuild must leave
    the view exactly where the user parked it.
    """
    state = AppState()
    monkeypatch.setattr(state, '_mark_dirty_and_save', lambda: None)
    note = state.create_note('Biology', 'Chlorophyll absorbs sunlight for photosynthesis.').value
    state.set_ai_settings({'base_url': 'https://example.test/v1', 'api_key': 'test-key'})
    session = _seed_long_session(state, note, turns=25)
    try:
        app = MainWindow(app_state=state)
    except tkinter.TclError as exc:
        pytest.skip(f'Tk display unavailable: {exc}')
    try:
        app.geometry('+3000+3000')
        app.deiconify()
        app.geometry('700x400')
        app.show_screen('coach')
        screen = app.screens['coach']
        canvas = screen._thread_canvas
        screen.resume_session(session.id)
        pump(app, lambda: len(screen.thread.winfo_children()) >= 25)
        assert _settle(app, screen)
        assert screen._thread_at_bottom()
        # User scrolls up into history...
        canvas.yview_scroll(-5000, 'units')
        assert not screen._stick_to_bottom
        parked = canvas.yview()[0]
        # ...then clicks buttons whose handlers end in refresh() with the
        # thread content unchanged (the old double-refresh of sessions(),
        # redundant manual refreshes).  Nothing may arm a pin window.
        from app.core.models import CoachTurn
        screen.refresh()
        screen.refresh()
        assert screen._pin_job is None and screen._settle_job is None, \
            'an identical re-render must not schedule any auto-scroll work'
        for _ in range(10):
            app.update()
            time.sleep(.02)
        assert abs(canvas.yview()[0] - parked) < 0.02, \
            'a same-content refresh dragged the view back to the bottom'
        # The real path: clicking "Sessions…" but picking nothing ends in
        # refresh() too — still identical content, still must not move.
        monkeypatch.setattr(screen, 'wait_window', lambda dialog: None)
        screen.sessions()
        assert screen._pin_job is None and screen._settle_job is None
        for _ in range(10):
            app.update()
            time.sleep(.02)
        assert abs(canvas.yview()[0] - parked) < 0.02
        # Sanity: genuine NEW content through the very same choke point DOES
        # arm a window and land at the bottom again.
        session.turns.append(CoachTurn.create('coach', 'question', 'Brand-new question.'))
        screen.refresh()
        assert screen._stick_to_bottom
        assert _settle(app, screen)
        assert screen._thread_at_bottom()
    finally:
        for s in app.screens.values():
            if hasattr(s, 'shutdown'):
                s.shutdown()
        app.destroy()


def test_shutdown_cancels_pending_scroll_jobs(monkeypatch):
    state = AppState()
    monkeypatch.setattr(state, '_mark_dirty_and_save', lambda: None)
    note = state.create_note('Biology', 'Chlorophyll absorbs sunlight for photosynthesis.').value
    state.set_ai_settings({'base_url': 'https://example.test/v1', 'api_key': 'test-key'})
    session = _seed_long_session(state, note, turns=10)
    try:
        app = MainWindow(app_state=state)
    except tkinter.TclError as exc:
        pytest.skip(f'Tk display unavailable: {exc}')
    try:
        app.geometry('+3000+3000')
        app.deiconify()
        app.show_screen('coach')
        screen = app.screens['coach']
        screen.resume_session(session.id)
        # Jobs are pending right after arming — shutdown must cancel both.
        assert screen._settle_job is not None
        screen.shutdown()
        assert screen._settle_job is None
        assert screen._pin_job is None
        app.update()   # would raise TclError if a cancelled job still fired
    finally:
        try:
            app.destroy()
        except tkinter.TclError:
            pass
