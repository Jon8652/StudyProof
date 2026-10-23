"""Run the two failing tests verbatim with tracing to find the veto source."""
import time, threading, tkinter
import app.gui.screens.coach_screen as cs_mod

# Wrap refresh() and _release_pin_for_manual_scroll globally for tracing
orig_refresh = cs_mod.CoachScreen.refresh
def traced_refresh(self):
    import traceback
    stack = [f"{f.filename.split('/')[-1]}:{f.lineno}:{f.name}" for f in traceback.extract_stack()[:-1][-5:]]
    print('REFRESH follow=%s stick=%s seq=%d base=%d pos=%.4f <- %s' % (
        self._pending_follow_event, self._stick_to_bottom, self._manual_scroll_seq,
        self._manual_scroll_baseline, self._thread_canvas.yview()[0] if self._thread_canvas else -9,
        ' <- '.join(stack)), flush=True)
    return orig_refresh(self)
cs_mod.CoachScreen.refresh = traced_refresh

orig_release = cs_mod.CoachScreen._release_pin_for_manual_scroll
def traced_release(self):
    import traceback
    stack = [f"{f.filename.split('/')[-1]}:{f.lineno}:{f.name}" for f in traceback.extract_stack()[:-1][-6:]]
    print('  RELEASE stick->False <- %s' % (' <- '.join(stack)), flush=True)
    return orig_release(self)
cs_mod.CoachScreen._release_pin_for_manual_scroll = traced_release

orig_check = cs_mod.CoachScreen._check_bottom_after_scroll
def traced_check(self, seq=None):
    print('  CHECK_BOTTOM seq=%s cur=%d at_bottom=%s' % (seq, self._manual_scroll_seq,
          self._thread_at_bottom() if self.winfo_exists() else '?'), flush=True)
    return orig_check(self, seq)
cs_mod.CoachScreen._check_bottom_after_scroll = traced_check

import pytest, sys
sys.argv = ['pytest', '-x', '-q', '-p', 'no:libtmux',
            'tests/test_coach_gui.py::test_button_clicks_never_move_the_view_even_with_new_content']
import runpy
runpy.run_module('pytest', run_name='__main__')
