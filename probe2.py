"""Probe 2: real dialogs, and dialog-closure events; log every viewport write."""
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
session = _seed_long_session(state, note)
app = MainWindow(app_state=state)
app.geometry('+3000+3000'); app.deiconify(); app.geometry('700x400')
app.show_screen('coach')
screen = app.screens['coach']
canvas = screen._thread_canvas

# Log EVERY yview request (auto or manual) with depth info
log = []
orig_yview = canvas.yview
def logging_yview(*a, **k):
    log.append((getattr(screen, '_auto_scroll_depth', '?'), a))
    return orig_yview(*a, **k)
canvas.yview = logging_yview
canvas.yview_scroll = lambda amount, what='units', *_r: logging_yview('scroll', amount, what)
canvas.yview_moveto = lambda f: logging_yview('moveto', f)

screen.resume_session(session.id)
assert pump(app, lambda: len(screen.thread.winfo_children()) >= 25)
assert settle(app, screen)
assert screen._thread_at_bottom()

canvas.yview_scroll(-3000, 'units')
pump(app, lambda: canvas.yview()[0] < 0.9)
print('parked:', round(canvas.yview()[0],4), 'stick:', screen._stick_to_bottom)

# --- Open the REAL Sessions dialog, then close it via WM delete ---
import threading
t = threading.Thread(target=screen.sessions, daemon=True)
# sessions() blocks in wait_window; run mainloop pumping in this thread instead:
screen.sessions.__wrapped__ = None
# simpler: schedule button press through after(), then close dialog after a delay
result_box = {}
def click_sessions():
    screen.sessions()
app.after(50, click_sessions)
deadline = time.monotonic() + 3
dlg = None
while time.monotonic() < deadline:
    app.update(); time.sleep(.01)
    for w in app.winfo_children():
        pass
    # find the dialog Toplevel
    toplevels = [w for w in app.winfo_toplevel().winfo_children()
                 if w.winfo_class() == 'Toplevel' and w.winfo_exists()]
    if len(toplevels) > len([w for w in app.winfo_toplevel().winfo_children() if w.winfo_class()=='Toplevel']):
        pass
    dlg = next((w for w in app.winfo_toplevel().winfo_children()
                if w.winfo_class() == 'Toplevel' and getattr(w, 'title', lambda: '')().startswith('Choose')), None)
    if dlg is not None:
        break
print('dialog found:', dlg)
before = canvas.yview()[0]
log.clear()
if dlg is not None:
    dlg.protocol('WM_DELETE_WINDOW', lambda: dlg.destroy())
    dlg.event_generate('<Destroy>')
    try:
        dlg.destroy()
    except tkinter.TclError:
        pass
for _ in range(30):
    app.update(); time.sleep(.02)
after = canvas.yview()[0]
print('sessions-dialog closed: before=%.4f after=%.4f drift=%.6f writes=%d' %
      (before, after, abs(after-before), len(log)))
print('write log:', log[:10])
print('stick now:', screen._stick_to_bottom, 'settle job:', screen._settle_job, 'pin job:', screen._pin_job)

for s in app.screens.values():
    if hasattr(s, 'shutdown'): s.shutdown()
app.destroy()
