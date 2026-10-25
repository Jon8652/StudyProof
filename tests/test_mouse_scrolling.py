"""Mouse events (not direct scroll commands) exercise every scrolling surface."""
import sys
import time
import tkinter
from datetime import date

import customtkinter as ctk
from PIL import Image
import pytest

from app.core.models import (CoachSession, CoachTurn, Dataset, Document, Note,
                             QuestionRecord, Session, SourceRef, WeakTopic)
from app.gui.dialogs.day_details_dialog import DayDetailsDialog
from app.gui.dialogs.note_editor_dialog import NoteEditorDialog
from app.gui.dialogs.sessions_dialog import SessionsDialog
from app.gui.dialogs.source_select import SourceSelectDialog
from app.gui.dialogs.weak_topics_dialog import WeakTopicsDialog
from app.gui.main_window import MainWindow
from app.gui.widgets.page_viewer import PageViewer
from app.state.app_state import AppState


def settle(root, ticks=8):
    for _ in range(ticks):
        root.update()
        time.sleep(.015)


def wheel(widget, up=False, modifier=''):
    if sys.platform.startswith('linux'):
        widget.event_generate(f'<{modifier}Button-{4 if up else 5}>')
    else:
        widget.event_generate(f'<{modifier}MouseWheel>', delta=120 if up else -120)


def click_bar(root, bar, fraction):
    canvas = bar._canvas
    horizontal = bar.cget('orientation') == 'horizontal'
    width, height = canvas.winfo_width(), canvas.winfo_height()
    x, y = (int(width * fraction), height // 2) if horizontal else (width // 2, int(height * fraction))
    canvas.event_generate('<Motion>', x=x, y=y)
    root.update()
    canvas.event_generate('<ButtonPress-1>', x=x, y=y)
    canvas.event_generate('<ButtonRelease-1>', x=x, y=y)
    settle(root, 2)


def drag_bar(root, bar):
    canvas = bar._canvas
    horizontal = bar.cget('orientation') == 'horizontal'
    length = canvas.winfo_width() if horizontal else canvas.winfo_height()
    cross = (canvas.winfo_height() if horizontal else canvas.winfo_width()) // 2
    start, end = bar.get()
    position = int((start + end) / 2 * length)
    x, y = (position, cross) if horizontal else (cross, position)
    canvas.event_generate('<Motion>', x=x, y=y)
    root.update()
    canvas.event_generate('<ButtonPress-1>', x=x, y=y)
    x, y = (int(length * .65), cross) if horizontal else (cross, int(length * .65))
    canvas.event_generate('<B1-Motion>', x=x, y=y, state=256)
    canvas.event_generate('<ButtonRelease-1>', x=x, y=y)
    settle(root, 2)


def assert_mouse_scrolling(root, canvas, bar, target, axis='y'):
    view = canvas.yview if axis == 'y' else canvas.xview
    move = canvas.yview_moveto if axis == 'y' else canvas.xview_moveto
    assert view()[1] - view()[0] < .95, 'fixture needs overflowing content'
    move(0)
    wheel(target, modifier='Shift-' if axis == 'x' else '')
    settle(root, 2)
    assert view()[0] > 0, f'wheel did not scroll {target}'
    down = view()[0]
    wheel(target, up=True, modifier='Shift-' if axis == 'x' else '')
    settle(root, 2)
    assert view()[0] < down, f'wheel up did not scroll {target}'
    move(0)
    wheel(bar._canvas)
    settle(root, 2)
    assert view()[0] > 0, 'wheel over scrollbar did not scroll'
    move(0)
    settle(root, 2)
    # Click below the thumb, including panes whose thumb fills most of the
    # track. A click inside the thumb correctly waits for a drag.
    click_bar(root, bar, (bar.get()[1] + 1) / 2)
    assert view()[0] > 0, 'clicking scrollbar track did not scroll'
    move(0)
    settle(root, 2)
    drag_bar(root, bar)
    assert view()[0] > 0, 'dragging scrollbar thumb did not scroll'


def first_label(frame):
    for child in frame.winfo_children():
        if isinstance(child, ctk.CTkLabel):
            return child._label
        try:
            return first_label(child)
        except LookupError:
            pass
    raise LookupError('No text label in scroll area')


@pytest.fixture
def mouse_app(monkeypatch):
    data = Dataset.empty()
    body = '\n'.join(f'Biology study detail {i}' for i in range(150))
    data.notes = [Note.create(f'Biology {i}', body, day=date.today()) for i in range(25)]
    data.sessions = [Session.create(10) for _ in range(25)]
    ref = SourceRef('note', data.notes[0].id, data.notes[0].title)
    for i in range(25):
        session = CoachSession.create([ref])
        q = QuestionRecord.create(f'Explain biology topic {i}.')
        session.questions.append(q)
        session.turns.append(CoachTurn.create('coach', 'question', q.prompt, q.id))
        data.coach_sessions.append(session)
        data.weak_topics.append(WeakTopic(str(i), ref, f'Biology topic {i}', q.prompt,
                                          session.id, q.id, 30))
        document = Document.create(f'Biology {i}.pdf', f'biology-{i}.pdf')
        document.set_extracted_text([(1, body)])
        data.documents.append(document)
    state = AppState(data)
    monkeypatch.setattr(state, '_mark_dirty_and_save', lambda: None)
    try:
        app = MainWindow(app_state=state)
    except tkinter.TclError as exc:
        pytest.skip(f'Tk display unavailable: {exc}')
    app.attributes('-alpha', 0)
    app.geometry('1100x700')
    errors = []
    app.report_callback_exception = lambda *args: errors.append(args[1])
    # Build Coach/Notes before Library: Library used to overwrite their
    # existing global handlers. Later screens must work in either order.
    for key in ('coach', 'notes', 'library', 'stats', 'settings'):
        app._get_screen(key)
    coach = app.screens['coach']
    sid = data.coach_sessions[0].id
    for _ in range(3):
        job = coach.service.prepare_recap(sid).value
        assert coach.service.complete_recap(job, coach.service.generate_recap(job)).ok
    coach.resume_session(sid)
    yield app, state, errors
    for screen in app.screens.values():
        if hasattr(screen, 'shutdown'):
            screen.shutdown()
    app.destroy()
    assert not errors, f'Tk callback errors: {errors}'


def test_all_screen_lists_wheel_track_click_and_thumb_drag(mouse_app):
    app, state, errors = mouse_app
    coach = app.screens['coach']
    app.show_screen('coach')
    settle(app)
    # Moving from canvas background onto a child generates Leave. Previously
    # that alone erased every screen's MouseWheel bindings for this root.
    coach._thread_canvas.event_generate('<Enter>')
    coach._thread_canvas.event_generate('<Leave>')
    for key, attribute in [('coach', 'thread'), ('notes', 'list_frame'),
                           ('library', 'list_scroll'), ('stats', 'scroll'), ('settings', 'content')]:
        app.show_screen(key)
        settle(app)
        frame = getattr(app.screens[key], attribute)
        assert_mouse_scrolling(app, frame._parent_canvas, frame._scrollbar, first_label(frame))
    # Coach shutdown must leave the other lists' native handlers intact.
    coach.shutdown()
    app.show_screen('notes')
    settle(app)
    frame = app.screens['notes'].list_frame
    frame._parent_canvas.yview_moveto(0)
    wheel(first_label(frame))
    settle(app)
    assert frame._parent_canvas.yview()[0] > 0


def test_all_dialog_lists_have_mouse_scrolling_and_leave_parent_intact(mouse_app):
    app, state, errors = mouse_app
    coach = app.screens['coach']
    constructors = [
        lambda: SessionsDialog(app, state, coach.service),
        lambda: SourceSelectDialog(app, state),
        lambda: WeakTopicsDialog(app, state),
        lambda: DayDetailsDialog(app, state, date.today()),
    ]
    for create in constructors:
        dialog = create()
        try:
            settle(app)
            frames = [dialog.scroll] if hasattr(dialog, 'scroll') else [dialog.list_frame, dialog.notes_frame]
            for frame in frames:
                assert_mouse_scrolling(app, frame._parent_canvas, frame._scrollbar, first_label(frame))
        finally:
            dialog.destroy()
    app.show_screen('notes')
    settle(app)
    frame = app.screens['notes'].list_frame
    frame._parent_canvas.yview_moveto(0)
    wheel(first_label(frame))
    settle(app)
    assert frame._parent_canvas.yview()[0] > 0


def test_recap_wheel_over_nested_text_and_buttons_cancels_pending_pin(mouse_app):
    app, state, errors = mouse_app
    coach = app.screens['coach']
    app.show_screen('coach')
    settle(app)
    canvas = coach._thread_canvas
    canvas.event_generate('<Enter>')
    canvas.event_generate('<Leave>')
    bubble = coach._rendered_turns[-1][1]
    targets = [bubble._wrapped_labels[0]._label, bubble.recap_buttons[0]._text_label]
    for target in targets:
        canvas.yview_moveto(1)
        coach._pending_follow_event = True
        coach.refresh()
        before = canvas.yview()[0]
        wheel(target, up=True)
        parked = canvas.yview()[0]
        assert parked < before
        assert not coach._stick_to_bottom
        settle(app, 15)
        assert canvas.yview()[0] == pytest.approx(parked, abs=.002)
    # Wheel input in another pane must not release a newly armed chat pin.
    coach._stick_to_bottom = True
    app.show_screen('notes')
    settle(app)
    wheel(first_label(app.screens['notes'].list_frame))
    assert coach._stick_to_bottom


def test_text_fields_scroll_independently_with_mouse(mouse_app):
    app, state, errors = mouse_app
    app.show_screen('notes')
    notes = app.screens['notes']
    notes._select(state.dataset.notes[0].id)
    settle(app)
    list_position = notes.list_frame._parent_canvas.yview()
    assert_mouse_scrolling(app, notes.detail_body._textbox, notes.detail_body._y_scrollbar,
                           notes.detail_body._textbox)
    assert notes.list_frame._parent_canvas.yview() == list_position
    dialog = NoteEditorDialog(app, state, note=state.dataset.notes[0])
    try:
        settle(app)
        box = dialog.body_text
        assert_mouse_scrolling(app, box._textbox, box._y_scrollbar, box._textbox)
    finally:
        dialog.destroy()
    app.show_screen('coach')
    coach = app.screens['coach']
    coach.composer.insert('1.0', state.dataset.notes[0].body)
    settle(app)
    chat_position = coach._thread_canvas.yview()
    assert_mouse_scrolling(app, coach.composer._textbox, coach.composer._y_scrollbar, coach.composer._textbox)
    assert coach._thread_canvas.yview() == chat_position
    app.show_screen('library')
    viewer = app.screens['library'].viewer
    viewer._show_stage('text')
    viewer.text_box.configure(state='normal')
    viewer.text_box.insert('1.0', state.dataset.notes[0].body)
    viewer.text_box.configure(state='disabled')
    settle(app)
    assert_mouse_scrolling(app, viewer.text_box._textbox, viewer.text_box._y_scrollbar, viewer.text_box._textbox)


def test_pdf_image_vertical_horizontal_zoom_and_other_panes_are_independent(mouse_app):
    app, state, errors = mouse_app
    app.show_screen('library')
    library = app.screens['library']
    viewer = library.viewer
    viewer._display_image(Image.new('RGB', (1600, 2400), 'white'))
    settle(app)
    before = library.list_scroll._parent_canvas.yview()
    assert_mouse_scrolling(app, viewer.image_canvas, viewer._image_vbar, viewer.image_label)
    assert library.list_scroll._parent_canvas.yview() == before
    assert_mouse_scrolling(app, viewer.image_canvas, viewer._image_hbar, viewer.image_label, axis='x')
    # The PDF binds both Linux wheel buttons even on Windows.
    viewer.image_canvas.yview_moveto(.5)
    before = viewer.image_canvas.yview()[0]
    viewer.image_label.event_generate('<Button-4>')
    settle(app, 2)
    assert viewer.image_canvas.yview()[0] < before
    viewer.image_label.event_generate('<Button-5>')
    settle(app, 2)
    assert viewer.image_canvas.yview()[0] == pytest.approx(before, abs=.01)
    zoom_events = []
    viewer._nudge_zoom = lambda steps: zoom_events.append(steps)
    wheel(viewer.image_label, up=True, modifier='Control-')
    assert zoom_events and zoom_events[-1] > 0
    zoom_events.clear()
    app.show_screen('notes')
    settle(app)
    wheel(first_label(app.screens['notes'].list_frame), up=True, modifier='Control-')
    assert not zoom_events, 'wheel in notes must not zoom the hidden PDF viewer'
    # A second viewer must not replace the app's wheel bindings either.
    binding = app.bind_all('<MouseWheel>')
    extra = PageViewer(app, library._events)
    extra.destroy()
    assert app.bind_all('<MouseWheel>') == binding
