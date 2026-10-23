"""Probe: does clicking Sessions.../Revise Weak Topics after a manual scroll move the view?"""
import os
import time, tkinter
from app.gui.main_window import MainWindow
from app.state.app_state import AppState
from app.core.models import CoachTurn


def _seed_long_session(state, note, turns=25):
    long_text = ('Photosynthesis converts light energy into chemical energy. ' * 12).strip()
    session = state.create_coach_session([state.source_ref_for('note', note.id)]).value
    for i in range(turns):
        role = 'coach' if i % 2 == 0 else 'user'
        kind = 'question' if role == 'coach' else 'answer'
        session.turns.append(CoachTurn.create(role, kind, f'{i}. {long_text}'))
    return session


def pump(app, predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.update()
        time.sleep(.01)
    return predicate()


def settle(app, screen, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.update()
        time.sleep(.02)
        if screen._settle_job is None and screen._pin_job is None:
            app.update()
            time.sleep(.08)
            if screen._settle_job is None and screen._pin_job is None:
                return True
    return False



if os.environ.get('DIALOGS') != '1':
    import app.gui.screens.coach_screen as cs
    class FakeDialog:
        result = None
        def __init__(self, *a, **k): pass
        def winfo_exists(self): return False
    cs.SessionsDialog = FakeDialog
    cs.WeakTopicsDialog = FakeDialog

state = AppState()
state._mark_dirty_and_save = lambda: None
note = state.create_note('Biology', 'Chlorophyll absorbs sunlight for photosynthesis.').value
state.set_ai_settings({'base_url': 'https://example.test/v1', 'api_key': 'test-key'})
session = _seed_long_session(state, note)
app = MainWindow(app_state=state)
app.geometry('+3000+3000')
app.deiconify()
app.geometry('700x400')
app.show_screen('coach')
screen = app.screens['coach']
canvas = screen._thread_canvas

moves = []
orig_yview_moveto = canvas.yview_moveto
orig_yview_scroll = canvas.yview_scroll
# record every viewport write AFTER wrapping happens at class level; use tk trace instead
try:
    from tkinter import Variable
except Exception:
    pass

screen.resume_session(session.id)
pump(app, lambda: len(screen.thread.winfo_children()) >= 25)
assert settle(app, screen)
assert screen._thread_at_bottom(), 'initial pin failed'

# User scrolls up into history mid-conversation.
canvas.yview_scroll(-3000, 'units')
pump(app, lambda: canvas.yview()[0] < 0.9)
parked = canvas.yview()[0]
print('parked fraction:', parked, 'stick:', screen._stick_to_bottom)

# Instrument: count automatic viewport writes during button-triggered refreshes.
count = {'writes': 0}
writelog = []
class TracingCanvasProxy:
    pass

# Wrap the tracked yview to log who calls it while auto depth changes
orig_scroll_thread = screen._scroll_thread_to_bottom
def logged_scroll():
    count['writes'] += 1
    writelog.append(('scroll_thread', canvas.yview()[0]))
    return orig_scroll_thread()
screen._scroll_thread_to_bottom = logged_scroll

orig_sync = screen._sync_scrollregion
def logged_sync():
    writelog.append(('sync', canvas.yview()[0]))
    return orig_sync()
screen._sync_scrollregion = logged_sync

# --- Scenario A: "Sessions..." dialog opens and closes WITHOUT picking ---
screen.wait_window = lambda dialog: None
before = canvas.yview()[0]
writelog.clear(); count['writes'] = 0
screen.sessions()
for _ in range(20):
    app.update(); time.sleep(.02)
after = canvas.yview()[0]
print('A sessions(no pick): before=%.4f after=%.4f drift=%.4f auto-scroll calls=%d' %
      (before, after, abs(after - before), count['writes']))
if writelog:
    print('   log:', writelog[:6])

# --- Scenario B: "Revise Weak Topics" click (no AI configured path aside, we have key;
# dialog closed without picking) ---
before = canvas.yview()[0]
writelog.clear(); count['writes'] = 0
screen.revise_weak_topics()
for _ in range(20):
    app.update(); time.sleep(.02)
after = canvas.yview()[0]
print('B revise_weak(no pick): before=%.4f after=%.4f drift=%.4f auto-scroll calls=%d' %
      (before, after, abs(after - before), count['writes']))

# --- Scenario C: assessment completion triggers refresh in background ---
from app.core.result import ActionResult
before = canvas.yview()[0]
writelog.clear(); count['writes'] = 0
job = screen.service.prepare_turn(session.id, 'feedback', conversational=True).value
screen.service.assessments[(job['session_id'], job['user_turn_id'])] = job
screen.assessment_finished(job, ActionResult(False, 'Background assessment unavailable.'))
for _ in range(20):
    app.update(); time.sleep(.02)
after = canvas.yview()[0]
print('C assessment_finished: before=%.4f after=%.4f drift=%.4f auto-scroll calls=%d' %
      (before, after, abs(after - before), count['writes']))
if writelog:
    print('   log:', writelog[:8])

for s in app.screens.values():
    if hasattr(s, 'shutdown'):
        s.shutdown()
app.destroy()
