"""Reproduce the exact failing-test sequence and trace every step."""
import time, threading, tkinter
from app.gui.main_window import MainWindow
from app.state.app_state import AppState
from app.core.models import CoachTurn

class GatedProvider:
    confidence = 'high'
    release = threading.Event()
    started = threading.Event()
    gate_kinds = ()
    def __init__(self, settings): pass
    def generate(self, context):
        from app.core.agent.schema import CoachReply
        if context['kind'] in self.gate_kinds:
            self.started.set()
            assert self.release.wait(5), 'provider was never released'
        return CoachReply(context['kind'], 'Confidence: high. Explain how sunlight supplies energy.')
    def assess_answer(self, context):
        from app.core.agent.schema import AnswerAssessment
        span = context['pack'].spans[0]
        return AnswerAssessment(span.source_id, 'Light and energy', context['question'].prompt, 95)

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

log = []
orig_yview = canvas.yview
def logging_yview(*a, **k):
    import traceback
    stack = traceback.extract_stack()[:-1]
    frames = [f"{f.filename.split('/')[-1]}:{f.lineno}:{f.name}" for f in stack[-8:] if 'diag3' not in f.filename]
    log.append((getattr(screen, '_auto_scroll_depth', '?'), a, frames))
    return orig_yview(*a, **k)
canvas.yview = logging_yview
canvas.yview_scroll = lambda amount, what='units', *_r: logging_yview('scroll', amount, what)
canvas.yview_moveto = lambda f: logging_yview('moveto', f)

# Trace veto checks
orig_sched = screen._schedule_thread_pin
def traced_sched():
    sup = screen._is_superseded_by_manual_scroll()
    log.append(('SCHED', ('superseded=%s stick=%s seq=%d base=%d' % (sup, screen._stick_to_bottom, screen._manual_scroll_seq, screen._manual_scroll_baseline)), []))
    return orig_sched()
screen._schedule_thread_pin = traced_sched

orig_apply = screen._apply_thread_pin
def traced_apply():
    log.append(('APPLY', ('stick=%s seq=%d base=%d' % (screen._stick_to_bottom, screen._manual_scroll_seq, screen._manual_scroll_baseline)), []))
    return orig_apply()
screen._apply_thread_pin = traced_apply

screen.resume_session(session.id)
assert pump(app, lambda: len(screen.thread.winfo_children()) >= 25)
assert settle(app, screen)
assert screen._thread_at_bottom()

canvas.yview_scroll(-5000, 'units')
pump(app, lambda: canvas.yview()[0] < 0.9)
parked = canvas.yview()[0]
print('parked:', round(parked,4), 'stick:', screen._stick_to_bottom)
log.clear()

# The real path like the test: stub wait_window, click sessions (no pick)
screen.wait_window = lambda dialog: None
screen.sessions()
print('after sessions(): stick=', screen._stick_to_bottom, 'pos=', round(canvas.yview()[0],4))
for e in log: print('  ', e[0], e[1])
log.clear()

# send() path with gated provider to emulate scripted reply
GatedProvider.release.set(); GatedProvider.started.clear(); GatedProvider.gate_kinds = ()
screen.service.live_factory = GatedProvider
screen.composer.insert('1.0', 'Energy from sunlight')
screen.send()
ok = pump(app, lambda: not screen.busy, timeout=6)
print('busy cleared:', ok)
for _ in range(10):
    app.update(); time.sleep(.02)
print('after send/reply: stick=', screen._stick_to_bottom, 'pos=', round(canvas.yview()[0],4),
      'seq=', screen._manual_scroll_seq, 'baseline=', screen._manual_scroll_baseline)
for e in log: print('  ', e[0], e[1], '<-'.join(e[2]))
