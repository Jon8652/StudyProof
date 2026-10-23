import sys, time
sys.path.insert(0, "tests"); sys.path.insert(0, ".")
import tkinter
from test_coach_gui import pump, GatedProvider, _settle
from app.state.app_state import AppState
from app.gui.main_window import MainWindow

state = AppState()
state._mark_dirty_and_save = lambda: None
note = state.create_note('Biology',
    'Photosynthesis converts sunlight into chemical energy stored in glucose, '
    'while chlorophyll absorbs the light needed for this process.').value
app = MainWindow(app_state=state)
GatedProvider.release.clear(); GatedProvider.started.clear()
try:
    app.geometry('+3000+3000'); app.deiconify(); app.geometry('700x400')
    app.show_screen('coach')
    screen = app.screens['coach']
    canvas = screen._thread_canvas
    state.set_ai_settings({'base_url': 'https://example.test/v1', 'api_key': 'test-key'})
    screen.service.live_factory = GatedProvider
    session = screen.service.start_session([('note', note.id)]).value
    screen.session_id = session.id
    GatedProvider.gate_kinds = ()
    screen.request('question')
    pump(app, lambda: not screen.busy)
    assert _settle(app, screen)
    from app.core.models import CoachTurn
    long_text = ('Energy explanation detail. ' * 30).strip()
    for i in range(20):
        session.turns.append(CoachTurn.create('coach' if i % 2 else 'user', 'notice', f'{i}. {long_text}'))
    screen._pending_follow_event = True
    screen.refresh()
    pump(app, lambda: len(screen.thread.winfo_children()) >= 21)
    assert _settle(app, screen)
    assert screen._thread_at_bottom()
    session.questions[-1].status = 'closed'
    GatedProvider.release.clear(); GatedProvider.started.clear()
    GatedProvider.gate_kinds = ('question','hint','feedback','followup')
    screen.request('question')
    pump(app, GatedProvider.started.is_set)
    assert screen.busy
    dbg = screen._dbg = []
    class _WheelUp:
        num = 4; delta = 120; widget = canvas
    screen._on_thread_wheel_event(_WheelUp())
    canvas.yview_scroll(-400, 'units')
    parked = canvas.yview()[0]
    print('parked', parked, 'seq', screen._manual_scroll_seq, 'baseline', screen._manual_scroll_baseline, 'stick', screen._stick_to_bottom)
    GatedProvider.release.set()
    pump(app, lambda: not screen.busy)
    print('after busy-clear: yview', canvas.yview()[0], 'stick', screen._stick_to_bottom, 'seq', screen._manual_scroll_seq, 'baseline', screen._manual_scroll_baseline)
    for i in range(10):
        app.update(); time.sleep(.02)
    final = canvas.yview()[0]
    print('final', final, 'stick', screen._stick_to_bottom)
    print('--- trace ---')
    for row in dbg:
        print(row)
finally:
    GatedProvider.release.set()
    for s in app.screens.values():
        if hasattr(s,'shutdown'): s.shutdown()
    app.destroy()
