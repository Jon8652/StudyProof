"""Diagnose why a legit follow-event pin gets vetoed after a manual scroll."""
import time, tkinter
from app.gui.main_window import MainWindow
from app.state.app_state import AppState
from app.core.models import CoachTurn

def pump(app, predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.update(); time.sleep(.01)
    return predicate()

def settle(app, screen, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.update(); time.sleep(.02)
        if screen._settle_job is None and screen._pin_job is None:
            app.update(); time.sleep(.08)
            if screen._settle_job is None and screen._pin_job is None:
                return True
    return False

state = AppState()
state._mark_dirty_and_save = lambda: None
note = state.create_note('Biology', 'Chlorophyll absorbs sunlight for photosynthesis.').value
state.set_ai_settings({'base_url': 'https://example.test/v1', 'api_key': 'test-key'})
long_text = ('Photosynthesis converts light energy into chemical energy. ' * 12).strip()
session = state.create_coach_session([state.source_ref_for('note', note.id)]).value
for i in range(25):
    role = 'coach' if i % 2 == 0 else 'user'
    kind = 'question' if role == 'coach' else 'answer'
    session.turns.append(CoachTurn.create(role, kind, f'{i}. {long_text}'))
app = MainWindow(app_state=state)
app.geometry('+3000+3000'); app.deiconify(); app.geometry('700x400')
app.show_screen('coach')
screen = app.screens['coach']
canvas = screen._thread_canvas

# Log every yview request with stack info
log = []
orig_yview = canvas.yview
def logging_yview(*a, **k):
    import traceback
    stack = traceback.extract_stack()[:-1]
    frames = [f"{f.filename.split('/')[-1]}:{f.lineno}:{f.name}" for f in stack[-6:]]
    log.append((getattr(screen, '_auto_scroll_depth', '?'), a, frames))
    return orig_yview(*a, **k)
canvas.yview = logging_yview
canvas.yview_scroll = lambda amount, what='units', *_r: logging_yview('scroll', amount, what)
canvas.yview_moveto = lambda f: logging_yview('moveto', f)

screen.resume_session(session.id)
assert pump(app, lambda: len(screen.thread.winfo_children()) >= 25)
assert settle(app, screen)
assert screen._thread_at_bottom()

canvas.yview_scroll(-5000, 'units')
pump(app, lambda: canvas.yview()[0] < 0.9)
parked = canvas.yview()[0]
print('parked:', round(parked,4), 'stick:', screen._stick_to_bottom, 'seq:', screen._manual_scroll_seq, 'baseline:', screen._manual_scroll_baseline)
log.clear()

# Now simulate the coach reply path exactly like finished() does
session.turns.append(CoachTurn.create('coach', 'question', 'Brand new reply bubble.'))
screen._pending_follow_event = True
screen.refresh()
for _ in range(15):
    app.update(); time.sleep(.02)
print('after reply: stick=', screen._stick_to_bottom, 'pos=', round(canvas.yview()[0],4),
      'seq=', screen._manual_scroll_seq, 'baseline=', screen._manual_scroll_baseline,
      'needs_pin=', screen._thread_needs_pin, 'pin_job=', screen._pin_job, 'settle=', screen._settle_job)
for depth, args, frames in log:
    print('YVIEW depth=%s args=%s' % (depth, args))
    print('   stack:', ' <- '.join(frames))
