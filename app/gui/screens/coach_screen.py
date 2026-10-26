"""Thin coaching UI; workers only generate, main thread commits."""
import threading
import time
from dataclasses import asdict

import customtkinter as ctk
from app.core.agent.coach_service import CoachService
from app.core.agent.grounding import PageIndex
from app.core.agent.provider_openai import sanitize_base_url
from app.core.agent.provider_base import ProviderError
from app.core.models import CoachSession, CoachTurn
from app.gui.screens.base_screen import BaseScreen
from app.gui.theme import BORDER, CARD_FG, PANE_BG
from app.gui.dialogs.source_select import SourceSelectDialog
from app.gui.dialogs.sessions_dialog import SessionsDialog
from app.gui.dialogs.weak_topics_dialog import WeakTopicsDialog
from app.gui.dialogs.message_dialog import show_message
from app.gui.widgets.chat_bubble import ChatBubble, target_wraplength, MIN_WRAPLENGTH
from app.gui.widgets.tooltip import Tooltip
from app.gui.workers import UIEventQueue

# Auto-scroll tuning (see the "Auto-scroll" section below).
BOTTOM_EPSILON_PX = 4   # tolerance so sub-pixel rounding/DPI scaling doesn't unstick the view
SETTLE_POLL_MS = 60     # interval of the post-rebuild settle poll
SETTLE_MAX_TICKS = 12   # ~0.7 s worst case; stops early once the height stabilises
# Grace window (ms) after the last content-extent change before the settle
# poll may terminate.  The tick budget alone is a race on platforms where
# CTk's deferred scrollregion rewrite and async bubble re-wraps land late
# (Windows): the poll would exit right before the final geometry burst,
# leaving the viewport parked off-bottom.  A short time-based settle keeps
# re-pinning until the extent has genuinely been quiet.
SETTLE_QUIET_MS = 300
# Window (ms) in which a pointer event must have been seen for a ``yview``
# write to count as a user gesture (see tracked_yview / _last_gesture_ms).
GESTURE_EVIDENCE_MS = 250


class CoachScreen(BaseScreen):
    def __init__(self, master, state):
        super().__init__(master, state, title='Study Coach')
        self.service = CoachService(state)
        self.session_id = None
        self.busy = False
        self._last_action = None
        self._suggestion_job = None
        self._suggestion_question = None
        self._assessment_notices = {}
        self._weak_topics_dialog = None
        self._recap_preview = None
        self._recap_loading_bubble = None
        self._resume_question_id = None
        self.events = UIEventQueue(self)
        self.events.start()
        # Pane 1 of three (change request: no grey surfaces).  Previously an
        # unstyled CTkFrame, which fell back to customtkinter's default grey
        # — very visible in light mode.  Now the blue PANE_BG surface with a
        # subtle border so it reads as a card around the top buttons.
        top = ctk.CTkFrame(self.content, corner_radius=12, fg_color=PANE_BG,
                           border_width=1, border_color=BORDER)
        top.pack(fill='x')
        ctk.CTkButton(top, text='New session', command=self.new_session).pack(side='left', padx=4, pady=4)
        ctk.CTkButton(top, text='Sessions…', command=self.sessions).pack(side='left', padx=4)
        ctk.CTkButton(top, text='Revise Weak Topics', command=self.revise_weak_topics).pack(side='left', padx=4)
        self.recap_button = ctk.CTkButton(top, text='Coach Recap', command=self.request_recap)
        self.recap_button.pack(side='left', padx=4, pady=4)
        self.recap_button.gate_tip = Tooltip(self.recap_button, 'A gentle summary of your last 7 days.')
        self.header = ctk.CTkLabel(self.content, text='', wraplength=MIN_WRAPLENGTH, justify='left')
        self.header.pack(fill='x')
        # Pane 2: the chat thread.  Unstyled CTkScrollableFrame used to show
        # customtkinter's default grey; now the same blue pane surface.
        self.thread = ctk.CTkScrollableFrame(self.content, fg_color=PANE_BG)
        self.thread.pack(fill='both', expand=True)
        self._rendered_session = None
        self._rendered_turns = []
        # Only session changes, sends and replies open a bounded follow
        # window. Controls-only refreshes retain both mapped messages and
        # any follow already in progress. Manual input cancels pending work.
        self._stick_to_bottom = True
        self._last_gesture_ms = 0
        self._rebuilding = False
        self._pending_follow_event = False
        self._pin_job = None
        self._settle_job = None
        self._settle_ticks = 0
        self._settle_deadline = 0.0
        self._last_extent_change = 0.0
        self._last_thread_height = -1
        self._thread_needs_pin = False
        self._thread_canvas = None
        self._manual_scroll_seq = 0
        self._auto_scroll_depth = 0
        self._manual_scroll_baseline = 0
        self._auto_move_count = 0  # viewport-write instrumentation for tests
        canvas = getattr(self.thread, '_parent_canvas', None)
        if canvas is not None:
            self._thread_canvas = canvas
            # customtkinter recomputes the scrollregion from a <Configure>
            # Tcl script bound on the inner frame (`scrollregion=bbox(all)`),
            # which only runs when the event queue drains — so right after a
            # destroy-and-repack the region still describes the *previous*
            # thread.  yview clamps against that stale region: too low after
            # growth (pin lands short of the newest bubble), too high after
            # switching to a shorter session (viewport parks over blank
            # space).  Every pin therefore refreshes the region synchronously
            # first (see _sync_scrollregion) — this early call just primes it.
            self._sync_scrollregion()
            # 1 px steps let _scroll_thread_to_bottom() land exactly on the
            # last bubble's bottom edge instead of CTk's default 30 px
            # Linux / Windows yscrollincrement.
            canvas.configure(yscrollincrement=1)
            # Wrap the canvas's ``yview`` command.  CTk wires BOTH the
            # scrollbar (``_scrollbar.command = canvas.yview``) and its own
            # ``bind_all`` wheel handler (``self._parent_canvas.yview(...)``)
            # to this method, so the wrapper is the one reliable observer of
            # every scroll request — including thumb drags and trackbar
            # clicks, which produce no wheel event at all.  Requests made by
            # our own automatic pins are tagged via ``_auto_scroll_depth``
            # and never treated as user gestures.
            orig_yview = canvas.yview

            def tracked_yview(*args, **kw):
                requested = bool(args) and str(args[0]).lower() in ('scroll', 'moveto')
                manual = False
                evidence = False
                if requested and self._auto_scroll_depth == 0:
                    # During replacement ignore layout-only writes, but
                    # always honour pointer gestures and direct scroll calls.
                    evidence = (time.monotonic() - self._last_gesture_ms) * 1000.0 \
                        <= GESTURE_EVIDENCE_MS
                    manual = evidence or not self._rebuilding
                if manual:
                    # Own the actual write, even if a wheel observer already
                    # released the pin.  A time-based "claimed" flag can
                    # swallow a later downward gesture in the same burst,
                    # preventing a return to bottom from being recognised.
                    seq = self._mint_manual_scroll_id()
                    if str(args[0]).lower() == 'scroll':
                        direction_up = float(args[1]) < 0
                    else:
                        direction_up = float(args[1]) < canvas.yview()[0]
                    self._release_pin_for_manual_scroll()
                    if not direction_up:
                        self.after_idle(lambda: self._check_bottom_after_scroll(seq))
                return orig_yview(*args, **kw)
            canvas.yview = tracked_yview
            # ``canvas.yview_scroll`` / ``yview_moveto`` are bound methods
            # captured straight from the Tk object — customtkinter's own
            # wheel handler (``_mouse_wheel_all`` below) and the internal
            # scrollbar callbacks call *those*, completely bypassing the
            # wrapper above.  Wrap them as thin shims that route through the
            # tracked command so EVERY scroll request — programmatic or
            # user-driven, from any caller — lands in one observable place.
            def tracked_yview_scroll(amount, what='units', *_rest):
                return tracked_yview('scroll', amount, what)

            def tracked_yview_moveto(fraction):
                return tracked_yview('moveto', fraction)
            canvas.yview_scroll = tracked_yview_scroll
            canvas.yview_moveto = tracked_yview_moveto
        # The owning window's bind tag runs before CTk's global wheel
        # handlers, including for labels/buttons nested inside recap bubbles.
        # Observe only our descendants and remove only our own callbacks on
        # shutdown. unbind_all would erase scrolling for every pane/dialog.
        self._wheel_bindings = []
        top = self.winfo_toplevel()
        for sequence in ('<MouseWheel>', '<Button-4>', '<Button-5>'):
            token = top.bind(sequence, self._observe_thread_wheel, add='+')
            self._wheel_bindings.append((top, sequence, token))
        # Scrollbar interaction channel: CTk routes thumb drags and trackbar
        # clicks through the scrollbar's command callback, which it wired to
        # ``canvas.yview`` at construction time — capturing the bound method
        # then would bypass tracked_yview entirely.  Repoint the scrollbar's
        # stored callback (CTkScrollbar keeps it as ``_command``) at a shim
        # that stamps gesture evidence and forwards to the wrapper.  A real
        # click/drag always runs the scrollbar's own bindings before this
        # callback fires; Tk's internal clamp writes never route through the
        # scrollbar, so evidence stamped here is genuine.
        scrollbar = getattr(self.thread, '_scrollbar', None)
        if scrollbar is not None:
            _orig_scroll_cmd = getattr(scrollbar, '_command', None)
            if _orig_scroll_cmd is not None:
                def _scrollbar_command(*args):
                    self._last_gesture_ms = time.monotonic()
                    return tracked_yview(*args)
                scrollbar._command = _scrollbar_command

            def _stamp_gesture(event=None):
                # customtkinter 6.0.0 only initialises the scrollbar's
                # _motion_center_offset inside its canvas tag Button-1
                # bindings (_clicked/_clicked_scrollbar).  This widget-level
                # add='+' handler also runs for <B1-Motion> events that reach
                # Tk before any tagged press (e.g. a press delivered while the
                # pointer is over the thumb gap), and Tk evaluates every
                # binding for the event — including CTk's own _on_motion on the
                # inner canvas — so an uninitialised offset there raises
                # AttributeError.  Stamping the default 0 here is harmless:
                # the real press handlers overwrite it before any drag uses it.
                if not hasattr(scrollbar, '_motion_center_offset'):
                    scrollbar._motion_center_offset = 0
                self._last_gesture_ms = time.monotonic()
            for seq in ('<ButtonPress-1>', '<ButtonPress-2>', '<ButtonPress-3>',
                        '<B1-Motion>', '<B2-Motion>'):
                scrollbar.bind(seq, _stamp_gesture, add='+')
        # Bubbles re-wrap their text asynchronously via their own Configure
        # handlers, so the thread keeps growing *after* refresh() has packed
        # everything; CTk's deferred scrollregion rewrite lands in the same
        # burst.  This fires when the frame's geometry changes — the
        # reliable trigger to re-pin while stuck at the bottom.  Bound with
        # add='+' it runs *after* CTk's plain bind on this same widget, so
        # the pin never gets clobbered by a later stale-region rewrite.
        self.thread.bind('<Configure>', self._on_thread_configure, add='+')
        # Responsive wrapping for the coach text: the header/notice labels
        # pack fill='x', so their allocated row width tracks the window.
        # Each label's wraplength is derived from its own width (floor only,
        # no ceiling), keeping the text flush with the box behind it whether
        # the window is small or maximised — matching how ChatBubble wraps.
        self._wrap_job = None
        self._responsive_labels = []
        # Bind on the content frame *and* each responsive label itself.  A
        # CTk widget is a plain Tk frame wrapping an internal canvas, so a
        # <Configure> delivered to the content binding can surface with
        # event.widget set to that inner canvas rather than the frame we
        # bound on.  The old identity filter (`event.widget is not
        # self.content`) then silently dropped every resize event and the
        # header/notice/confidence rows stayed pinned at their floor width.
        # Binding each label directly means the event always concerns that
        # label's own row, so no filtering is needed here at all.
        self.content.bind('<Configure>', self._schedule_wrap_update, add='+')
        # Private background ratings never appear in the conversation.
        self.confidence_note = ctk.CTkLabel(self.content, text='', text_color='#888888',
                                            wraplength=MIN_WRAPLENGTH)
        self.confidence_note.pack(fill='x', pady=(2, 0))
        self.suggestions = ctk.CTkFrame(self.content, fg_color='transparent')
        ctk.CTkLabel(self.suggestions, text='Need a starting point?',
                     text_color='#888888').pack(anchor='w')
        for label, starter in [
            ('Explain in my own words', 'In my own words, '),
            ('Start with what I remember', 'What I remember is '),
            ('Describe what is unclear', "I'm unsure about ")]:
            ctk.CTkButton(self.suggestions, text=label, height=28,
                          command=lambda text=starter: self.use_suggestion(text)).pack(
                              side='left', padx=3, pady=3)
        # Blue pane surface for the input area (was default grey, very visible
        # in light mode).  The textbox itself sits slightly lighter on the pane.
        composer_row = ctk.CTkFrame(self.content, corner_radius=18,
                                    fg_color=PANE_BG, border_width=1,
                                    border_color=BORDER)
        composer_row.pack(fill='x', pady=(4, 0))
        self.composer = ctk.CTkTextbox(composer_row, height=85, border_width=0,
                                       fg_color=CARD_FG)
        self.composer.pack(side='left', fill='x', expand=True, padx=6, pady=6)
        self.composer.bind('<Return>', self.send)
        self.composer.bind('<Shift-Return>', lambda event: None)
        self.composer.bind('<KeyRelease>', self._typing, add='+')
        self.composer.bind('<KeyPress>', self._composer_input, add='+')
        self.composer.bind('<<Paste>>', self._composer_paste, add='+')
        self.send_button = ctk.CTkButton(
            composer_row, text='↑', width=42, height=42, corner_radius=21,
            font=ctk.CTkFont(size=26), command=self.send)
        self.send_button.pack(side='right', padx=10)
        self.send_button.gate_tip = Tooltip(self.send_button, 'Send · Enter (Shift+Enter for a new line)')
        row = ctk.CTkFrame(self.content, fg_color='transparent')
        row.pack(fill='x', pady=5)
        ctk.CTkLabel(row, text='Enter to send · Shift+Enter for a new line',
                     text_color='#888888').pack(side='left', padx=4)
        self.buttons = {}
        for key, label, command in [
            ('question', 'Next question', lambda: self.request('question')),
            ('hint', 'Hint', lambda: self.request('hint')),
            ('followup', 'Follow-up', lambda: self.request('followup')),
            ('cancel', 'Cancel', self.cancel)]:
            button = ctk.CTkButton(row, text=label, width=90, command=command)
            self.buttons[key] = button
            button.gate_tip = Tooltip(button, '')
        self.notice = ctk.CTkLabel(self.content, text='', wraplength=MIN_WRAPLENGTH)
        self.notice.pack(fill='x')
        self._responsive_labels = [self.header, self.notice, self.confidence_note]
        # Each responsive row also gets its own <Configure> binding (see the
        # comment where the content binding is created): the event concerns
        # that label's allocated row directly, so it can never be dropped by
        # a widget-identity check.
        for label in self._responsive_labels:
            label.bind('<Configure>', self._schedule_wrap_update, add='+')
        self.refresh()

    # ------------------------------------------------------------------
    # Responsive coach-text wrapping

    def _schedule_wrap_update(self, event=None):
        # Debounce resize storms: only the last Configure in a burst does
        # the (cheap, guarded) relayout work.  No widget-identity filtering:
        # every binding that reaches this handler (the content frame and
        # each responsive label) concerns geometry that _apply_responsive_
        # wrapping reads directly from the labels themselves.
        if self._wrap_job is not None:
            try:
                self.after_cancel(self._wrap_job)
            except Exception:
                pass
        self._wrap_job = self.after(50, self._apply_responsive_wrapping)

    def _apply_responsive_wrapping(self):
        self._wrap_job = None
        if not self.winfo_exists():
            return
        try:
            scaling = float(self._get_widget_scaling()) or 1.0
        except Exception:
            scaling = 1.0
        content_width = self.content.winfo_width()
        for label in self._responsive_labels:
            try:
                if not label.winfo_exists():
                    continue
                # Prefer the label's own allocated row width so each text
                # block hugs its own box; fall back to the screen content
                # width while the label is still un-mapped.
                width = label.winfo_width()
                if width <= 1:
                    width = content_width
                if width <= 1:
                    continue              # nothing measured yet
                target = target_wraplength(width, scaling)
                if label.cget('wraplength') != target:
                    label.configure(wraplength=target)
            except Exception:
                pass                      # widget torn down mid-resize

    def new_session(self):
        if not self._require_connection():
            return
        if self.busy:
            self.cancel(cancel_assessments=False)
        dialog = SourceSelectDialog(self, self.state)
        self.wait_window(dialog)
        if dialog.result:
            result = self.service.start_session(dialog.result)
            if result.ok:
                self.session_id = result.value.id
                self._resume_question_id = None
                # Opening a brand-new session is a conversation event: land
                # at its newest message.  Without this flag the first
                # refresh() of the (short) new thread renders inertly while
                # the viewport keeps the OLD scroll fraction from the
                # previous, much longer session — which maps to the top of a
                # near-empty canvas, i.e. the "blank until I scroll" glitch.
                # request() deliberately never arms anything itself (button
                # clicks must not move the view); the events declare
                # themselves here, mirroring revise_topic()/resume_session().
                self._pending_follow_event = True
                self.request('question')
            else:
                self.notice.configure(text=result.message)

    def sessions(self):
        if self.busy:
            self.cancel(cancel_assessments=False)
        dialog = SessionsDialog(self, self.state, self.service)
        self.wait_window(dialog)
        if dialog.result:
            # resume_session() refreshes itself; the extra refresh() used to
            # re-render an identical thread and — because every rebuild armed
            # a pin window — yank the viewport back to the bottom after a
            # manual scroll whenever the button was pressed.
            self.resume_session(dialog.result)
        else:
            self.refresh()

    def revise_weak_topics(self):
        if not self._require_connection():
            return
        self.cancel(cancel_assessments=False)
        dialog = WeakTopicsDialog(self, self.state)
        self._weak_topics_dialog = dialog
        self.wait_window(dialog)
        self._weak_topics_dialog = None
        if dialog.result:
            self.revise_topic(dialog.result)

    def revise_topic(self, topic_id):
        """Open the same topic revision from the picker or Statistics."""
        if not self._require_connection():
            return
        self.cancel(cancel_assessments=False)
        result = self.service.start_weak_topic_session(topic_id)
        if result.ok:
            self.session_id = result.value.id
            self._resume_question_id = None
            self.notice.configure(text='Answer in your own words, or ask for help with this topic.')
            # Opening a new session is a conversation event: land at its
            # newest message (absolute pin — direction never matters).
            self._pending_follow_event = True
            self.refresh()
        else:
            self.notice.configure(text=result.message)

    def resume_session(self, sid):
        switched = sid != self.session_id
        self.cancel(cancel_assessments=False)
        self.session_id = sid
        self._resume_question_id = None
        # Switching sessions is a conversation event: always open the view at
        # its newest message, whether reaching it means scrolling up or down.
        self._pending_follow_event = switched
        self.refresh()

    def request(self, kind):
        if not self._require_connection():
            return
        self._last_action = kind
        session = self.state.coach_session_by_id(self.session_id)
        prepared = self.service.prepare_turn(
            self.session_id, kind, question_id=self._resume_question_id,
            conversational=bool(session and session.turns))
        if not prepared.ok:
            self.notice.configure(text=prepared.message)
            return
        job = prepared.value
        self.busy = True
        self.notice.configure(text='Coach is thinking…')
        self.refresh()
        if self.service.prepare_assessment(job):
            def assess():
                assessment = self.service.generate_assessment(job)
                self.events.post(lambda: self.assessment_finished(job, assessment))
            threading.Thread(target=assess, daemon=True).start()
        def run():
            # Poll the cancel flag while the (possibly slow) provider call is
            # in flight so Cancel returns promptly instead of waiting for the
            # turn to finish.  A cancelled turn posts nothing at all.
            generate = threading.Thread(
                target=lambda: setattr(run, 'result', self.service.generate_turn(job)),
                daemon=True)
            generate.start()
            while generate.is_alive():
                if job['cancel'].is_set():
                    return
                generate.join(0.05)
            if job['cancel'].is_set():
                return
            self.events.post(lambda: self.finished(job, run.result))
        threading.Thread(target=run, daemon=True).start()

    def request_recap(self, days=7):
        if self.busy:
            return
        prepared = self.service.prepare_recap(self.session_id, days)
        if not prepared.ok:
            self.notice.configure(text=prepared.message)
            return
        job = prepared.value
        self.busy = True
        self._hide_suggestions()
        self.notice.configure(text='')
        self.refresh()
        loading = CoachTurn.create('coach', 'notice', 'Putting together your recap…')
        self._recap_loading_bubble = ChatBubble(self.thread, loading, [], None, self.navigate)
        self._recap_loading_bubble.pack(fill='x', pady=4)
        self._stick_to_bottom = True
        self._reset_manual_scroll_baseline()
        self._schedule_thread_pin()
        self._start_settle_poll()
        def run():
            try:
                result = self.service.generate_recap(job)
            except Exception:
                from app.core.result import ActionResult
                result = ActionResult(False, 'Could not assemble this recap. Please try again.')
            if not job['cancel'].is_set():
                self.events.post(lambda: self.recap_finished(job, result))
        threading.Thread(target=run, daemon=True).start()

    def _clear_recap_loading(self):
        if self._recap_loading_bubble is not None:
            self._recap_loading_bubble.destroy()
            self._recap_loading_bubble = None

    def recap_finished(self, job, result):
        if self.service.pending.get(job['session_id']) is not job:
            return
        self._clear_recap_loading()
        self.busy = False
        result = self.service.complete_recap(job, result)
        if result.ok and self.session_id is None:
            if self._recap_preview is None:
                self._recap_preview = CoachSession(id='recap-preview')
            self._recap_preview.turns.append(result.value)
        self.notice.configure(text=result.message or (
            'Open a saved conversation before requesting a recap to keep it in that conversation.'
            if self.session_id is None else ''))
        self._pending_follow_event = True
        self.refresh()

    def recap_offer(self, turn, accepted):
        if self.busy or not turn.recap or not turn.recap.get('offer'):
            return
        turn.recap['offer'] = False
        session = self.state.coach_session_by_id(self.session_id)
        if session:
            self.state.persist_coach_session(session)
        self.refresh()
        if accepted:
            self.request_recap(30)

    def resume_recap_question(self, link):
        session = self.state.coach_session_by_id(link['session_id'])
        if not session:
            self.notice.configure(text='That session was deleted.')
            return
        question = session.question_by_id(link['question_id'])
        if not question:
            from app.core.agent.questions import question_entries
            from app.core.models import QuestionRecord
            entry = next((item for item in question_entries(session)
                          if item['question_id'] == link['question_id']), None)
            if not entry:
                self.notice.configure(text='That question is no longer available.')
                return
            # Recover a literal question from older natural replies only
            # after the learner chooses Resume; requesting a recap is read-only.
            question = QuestionRecord(id=entry['question_id'], prompt=entry['prompt'])
            session.questions.append(question)
            self.state.persist_coach_session(session)
        if question.status == 'closed':
            from app.core.agent.questions import question_entries
            pending = next((item for item in question_entries(session)
                            if item['question_id'] == question.id and not item['answered']), None)
            if pending:
                question.status = 'open'
                self.state.persist_coach_session(session)
        self.resume_session(session.id)
        self._resume_question_id = question.id
        self.refresh()
        self._pending_follow_event = False
        self._release_pin_for_manual_scroll()
        def scroll():
            if self.session_id != session.id or not self.winfo_exists():
                return
            self.update_idletasks()
            self._sync_scrollregion()
            turns = [t for t in session.turns if t.kind != 'confidence']
            for turn, (_, bubble) in zip(turns, self._rendered_turns):
                if (turn.id == link.get('turn_id') or
                        (turn.question_id == question.id and turn.role == 'coach'
                         and turn.kind in ('question', 'followup', 'feedback'))):
                    height = max(1, self._content_bottom())
                    self._auto_scroll_depth += 1
                    try:
                        self.thread._parent_canvas.yview_moveto(bubble.winfo_y() / height)
                    finally:
                        self._auto_scroll_depth -= 1
                    self.composer.focus_set()
                    break
        self.after_idle(scroll)

    def assessment_finished(self, job, result):
        result = self.service.complete_assessment(job, result)
        if self._weak_topics_dialog and self._weak_topics_dialog.winfo_exists():
            self._weak_topics_dialog.rebuild()
        if not result.ok and result.message.startswith('Background assessment'):
            self._assessment_notices[job['session_id']] = result.message
            if not self.busy and self.session_id == job['session_id']:
                self.notice.configure(text=self._assessment_notices.pop(job['session_id']))
        self.refresh()

    def finished(self, job, result):
        if self.service.pending.get(job['session_id']) is not job:
            return
        self.busy = False
        result = self.service.complete_turn(job, result)
        if result.ok and result.value is not None:
            self._resume_question_id = result.value.id
        self.notice.configure(text=result.message or self._assessment_notices.pop(job['session_id'], ''))
        # A new coach reply just landed in the thread — a conversation event.
        self._pending_follow_event = True
        self.refresh()

    def cancel(self, cancel_assessments=True):
        self.service.cancel_turn(self.session_id, cancel_assessments=cancel_assessments)
        self._clear_recap_loading()
        self.busy = False
        self.notice.configure(text='Turn cancelled.')
        # Cancellation changes the controls, not the conversation.  In
        # particular, opening a picker while busy must not arm a scroll.
        self.refresh()

    def _submitted(self, result):
        self.notice.configure(text=result.message or '')
        if result.ok:
            self.composer.delete('1.0', 'end')
        # The user's message was appended (or a revision rewritten) — a
        # conversation event; land at the newest bubble.
        if result.ok:
            self._pending_follow_event = True
        self.refresh()

    def answer(self):
        self._submitted(self.service.submit_answer(self.session_id, self.composer.get('1.0', 'end')))

    def revise(self):
        self._submitted(self.service.submit_revision(self.session_id, self.composer.get('1.0', 'end')))

    def send(self, event=None):
        """Send a study message and continue the source-grounded conversation."""
        if self.busy:
            return 'break'
        if not self._require_connection():
            return 'break'
        session = self.state.coach_session_by_id(self.session_id)
        q = (session.question_by_id(self._resume_question_id) if session and self._resume_question_id
             else session.questions[-1] if session and session.questions else None)
        text = self.composer.get('1.0', 'end').strip()
        if not q or q.status == 'closed':
            self.notice.configure(text='Choose New session and select your notes or PDFs to get a question first.')
            return 'break'
        if not text:
            if q.first_answer is not None and not q.feedback_given:
                self.request('feedback')
            return 'break'
        result = self.service.submit_message(self.session_id, text, self._resume_question_id)
        self._submitted(result)
        if result.ok:
            self.request('feedback')
        return 'break'

    def _hide_suggestions(self):
        if self._suggestion_job is not None:
            self.after_cancel(self._suggestion_job)
            self._suggestion_job = None
        self.suggestions.pack_forget()

    def _typing(self, event=None):
        self._hide_suggestions()
        self._schedule_suggestions()

    def _schedule_suggestions(self):
        session = self.state.coach_session_by_id(self.session_id)
        q = (session.question_by_id(self._resume_question_id) if session and self._resume_question_id
             else session.questions[-1] if session and session.questions else None)
        if q and q.first_answer is None and not self.busy and self._suggestion_job is None:
            self._suggestion_job = self.after(15_000, self._show_suggestions)

    def _show_suggestions(self):
        self._suggestion_job = None
        session = self.state.coach_session_by_id(self.session_id)
        q = (session.question_by_id(self._resume_question_id) if session and self._resume_question_id
             else session.questions[-1] if session and session.questions else None)
        if (q and q.first_answer is None and not self.busy and self.winfo_viewable()
                and not self.composer.get('1.0', 'end').strip()):
            self.suggestions.pack(fill='x', before=self.composer.master, pady=4)

    def use_suggestion(self, text):
        if not self._require_connection():
            return
        self._hide_suggestions()
        if not self.composer.get('1.0', 'end').strip():
            self.composer.insert('1.0', text)
            self.composer.focus_set()

    def navigate(self, quote):
        root = self.winfo_toplevel()
        session = self.state.coach_session_by_id(self.session_id)
        ref = next((r for r in session.sources if r.id == quote['source_id']), None)
        if ref and ref.kind == 'document':
            root.show_screen('library')
            screen = root.screens['library']
            screen.select_document(ref.id)
            screen.viewer.go_to_page(quote['page'])
        elif ref:
            root.show_screen('notes')
            root.screens['notes']._select(ref.id)

    def _connection_configured(self):
        ai = self.state.settings.ai
        configured = bool(ai.api_key.strip() and ai.model.strip())
        try:
            sanitize_base_url(ai.base_url)
        except ProviderError:
            configured = False
        return configured

    def _require_connection(self):
        if self._connection_configured():
            return True
        message = ('Connect an AI model to use Study Coach. Open Settings, add your '
                   'Base URL and API key, choose a model, then click Test connection.')
        self.notice.configure(text=message)
        show_message(self.winfo_toplevel(), 'Connect an AI model', message)
        return False

    def _composer_input(self, event):
        # Reading, selecting and scrolling saved chats requires no AI setup.
        # Only an attempt to enter text should prompt for a connection.
        if event.char and event.char.isprintable() and not self.busy:
            if not self._require_connection():
                return 'break'

    def _composer_paste(self, event=None):
        if not self.busy and not self._require_connection():
            return 'break'

    def refresh(self):
        session = self.state.coach_session_by_id(self.session_id)
        if session is None and self.session_id is None:
            session = self._recap_preview
        q = (session.question_by_id(self._resume_question_id) if session and self._resume_question_id
             else session.questions[-1] if session and session.questions else None)
        # Events declare scrolling explicitly. Widget reconciliation below
        # is independent: detecting a changed turn never arms a follow.
        follow_event = self._pending_follow_event
        self._pending_follow_event = False
        dbg = getattr(self, '_dbg', None)
        if dbg is not None:
            dbg.append(('refresh', follow_event, 'seq', self._manual_scroll_seq,
                        'base', self._manual_scroll_baseline,
                        'stick', self._stick_to_bottom))
        # A controls-only refresh must also leave any legitimate send/reply
        # follow in flight: send() calls request() before geometry settles.
        # Dismiss any visible tooltip before re-laying out the thread; stale
        # popups must never survive a state change or a moved pointer.  The
        # shared popup window is only hidden (never destroyed) here, which is
        # what keeps rapid refresh/hover churn from crashing Tk on Windows.
        Tooltip.dismiss_all()
        if session:
            notes = {n.id: n for n in self.state.dataset.notes}
            docs = {d.id: d for d in self.state.dataset.documents}
            index = PageIndex(session.sources, notes, docs)
            labels = []
            for ref in session.sources:
                exists = ref.id in (notes if ref.kind == 'note' else docs)
                labels.append(ref.label + (f' ({docs[ref.id].page_count} pages)' if ref.id in docs else '')
                              + ('' if exists else ' (removed)'))
            mode = 'live'
            self.header.configure(text=(f'{mode.title()} · ' + ' · '.join(labels)) if labels else 'Coach Recap')
            self._refresh_thread(session, index)
        else:
            self.header.configure(text='Pick sources to start a coached session.')
            self._refresh_thread(None, None)
        # Keep guidance visible while the separate assessment stays private.
        if q and q.first_answer is not None:
            note = 'Ask a question, try again, or choose another topic from your sources.'
        else:
            note = ''
        self.confidence_note.configure(text=note)
        question_key = (self.session_id, q.id if q else None)
        if question_key != self._suggestion_question or self.busy or not q or q.first_answer is not None:
            self._hide_suggestions()
            self._suggestion_question = question_key
        self._schedule_suggestions()
        gates = {
            'question': bool(self.session_id and session) and (not q or q.feedback_given),
            'hint': bool(q and q.first_answer is not None and not q.feedback_given and q.status != 'closed'),
            'followup': bool(q and q.feedback_given and q.status != 'closed'),
            'cancel': self.busy}
        for key, enabled in gates.items():
            button = self.buttons[key]
            button.pack_forget()
            if enabled and (not self.busy or key == 'cancel'):
                button.pack(side='right', padx=2)
                button.configure(state='normal')
        # Keep Send actionable so setup and session guidance can be shown.
        can_send = not self.busy
        self.send_button.configure(state='normal' if can_send else 'disabled')
        self.composer.configure(state='disabled' if self.busy else 'normal')
        self.recap_button.configure(state='disabled' if self.busy else 'normal')
        for _, bubble in self._rendered_turns:
            for button in bubble.recap_buttons:
                button.configure(state='disabled' if self.busy else 'normal')
        # A controls refresh leaves this state alone; in particular it must
        # not cancel the follow opened by the immediately preceding send.
        if session is not None and follow_event:
            # New conversation content or switching to a session.  It
            # ALWAYS lands at its bottom — absolute pinning, and any
            # manual-scroll veto from before this event expires here.
            # Switching to a shorter session scrolls *up* if the old view
            # was parked mid-scroll; direction never matters.
            self._stick_to_bottom = True
            self._thread_needs_pin = True
            self._reset_manual_scroll_baseline()
            # Defer the pin until Tk's geometry manager has laid out the
            # freshly packed bubbles.  In this very cycle every extent we
            # could measure (canvas bbox, frame height) still describes the
            # *previous* thread: pinning now clamps against stale numbers —
            # too low after growth (newest bubble stays out of view), too
            # high after switching to a shorter session (viewport parks over
            # blank space).  The idle pass runs right after the queued
            # Configure burst, and the settle poll + <Configure> hook below
            # then keep re-pinning while async bubble re-wraps land.
            self._schedule_thread_pin()
            self._start_settle_poll()

    def _refresh_thread(self, session, index):
        """Keep the unchanged prefix mapped; append only the new bubbles.

        Destroying the whole thread on a controls-only refresh collapses its
        height and exposes the canvas background before the replacement
        labels wrap.  Restoring a scroll fraction afterwards cannot prevent
        that flash.  Retaining the widgets preserves their geometry instead.
        Store value snapshots so edits to persisted turns/quotes are detected.
        """
        turns = [t for t in session.turns if t.kind != 'confidence'] if session else []
        sources = [asdict(ref) for ref in session.sources] if session else []
        keys = [
            (asdict(turn), sources,
             [index.page(q['source_id'], q['page']) for q in turn.quotes])
            for turn in turns
        ]
        keep = 0
        sid = session.id if session else None
        if sid == self._rendered_session:
            for key, (previous, bubble) in zip(keys, self._rendered_turns):
                if key != previous:
                    break
                keep += 1
        if keep == len(keys) == len(self._rendered_turns):
            self._rendered_session = sid
            return
        self._rebuilding = True
        try:
            for _, bubble in self._rendered_turns[keep:]:
                bubble.destroy()
            self._rendered_turns[keep:] = []
            for turn, key in zip(turns[keep:], keys[keep:]):
                bubble = ChatBubble(self.thread, turn, session.sources, index, self.navigate,
                                    recap_actions={'resume': self.resume_recap_question,
                                                   'revise': self.revise_weak_topics,
                                                   'offer': self.recap_offer})
                bubble.pack(fill='x', pady=4)
                self._rendered_turns.append((key, bubble))
            self._rendered_session = sid
        finally:
            self.after_idle(lambda: setattr(self, '_rebuilding', False))

    # ------------------------------------------------------------------
    # Auto-scroll ("stick to bottom")

    def _sync_scrollregion(self):
        """Recompute the canvas scrollregion from the current content bbox.

        customtkinter refreshes the region only from a ``<Configure>`` Tcl
        script bound on the inner frame, which runs when the event queue is
        drained — so right after a destroy-and-repack it still describes the
        *previous* thread.  yview clamps against that stale region: too low
        after growth (the pin lands short of the newest bubble), too high
        after switching to a shorter session (the viewport parks over blank
        space).  Pull the update forward before every pin.  Returns True when
        the region was refreshed.
        """
        canvas = self._thread_canvas
        if canvas is None or not canvas.winfo_exists():
            return False
        try:
            bbox = canvas.bbox('all')
            if bbox is not None:
                canvas.configure(scrollregion=bbox)
            return True
        except Exception:
            return False                  # torn down mid-relayout

    def _content_bottom(self):
        """Bottom of the allocated canvas content, never requested sizes.

        New labels can request a height very different from their eventual
        wrapped height.  Combining that estimate with the old scrollregion
        can scroll beyond every mapped bubble and show an empty viewport.
        Configure events schedule another pin when actual geometry grows.
        """
        canvas = self._thread_canvas
        if canvas is not None and canvas.winfo_exists():
            region = canvas.bbox('all')
            if region is not None:
                return region[3]
        return 0

    def _viewport_top(self, canvas=None):
        """Tk's actual viewport origin in canvas pixels."""
        canvas = canvas if canvas is not None else self._thread_canvas
        if canvas is None or not canvas.winfo_exists():
            return 0.0
        return float(canvas.canvasy(0))

    def _manual_scroll_to_bottom(self, canvas):
        """Legacy pin: clamp Tk's own yview against the (synced) region."""
        self._sync_scrollregion()
        try:
            canvas.yview_scroll(10_000_000, 'units')
        except Exception:
            try:
                canvas.yview_moveto(1.0)
            except Exception:
                pass

    def _thread_at_bottom(self):
        """True when the newest bubble is fully visible in the viewport.

        Position-based (the actual top edge of the viewport vs. the real
        content extent), never the fractional ``yview()`` values: those are
        ratios of a possibly stale scrollregion and report "at bottom"
        spuriously right after any rebuild.
        """
        canvas = self._thread_canvas
        if canvas is None or not canvas.winfo_exists():
            return True
        try:
            height = canvas.winfo_height()
            if height <= 1:
                return True                 # un-mapped: nothing to judge yet
            bottom = self._content_bottom()
            if bottom <= 0:
                return True                 # empty thread — trivially pinned
            top = self._viewport_top(canvas)
            return bottom - (top + height) <= BOTTOM_EPSILON_PX
        except Exception:
            return True                     # torn down mid-read

    def _scroll_thread_to_bottom(self):
        """Pin against allocated geometry; newer manual input always wins."""
        canvas = self._thread_canvas
        if canvas is None or not canvas.winfo_exists():
            return
        if self._is_superseded_by_manual_scroll():
            # The user scrolled after this pin was scheduled — leave the
            # viewport exactly where they put it.
            self._release_pin_for_manual_scroll()
            return
        self._auto_scroll_depth += 1
        try:
            self._pin_viewport(canvas)
        except Exception:
            try:
                self._manual_scroll_to_bottom(canvas)   # legacy fallback
            except Exception:
                pass
        finally:
            self._auto_scroll_depth -= 1

    def _bottom_gap(self, canvas):
        """Pixels between the viewport bottom edge and the content extent.

        Positive means the newest content still hangs below the viewport;
        zero/negative means it is fully visible.  Returns None when nothing
        can be judged yet (un-mapped canvas or empty thread).
        """
        height = canvas.winfo_height()
        if height <= 1:
            return None                 # un-mapped: nothing to judge yet
        bottom = self._content_bottom()
        if bottom <= 0:
            return None                 # empty thread — trivially pinned
        return bottom - (self._viewport_top(canvas) + height)

    def _pin_viewport(self, canvas):
        """Follow allocated, mapped messages while child geometry catches up."""
        unmapped = canvas.winfo_height() <= 1
        self._sync_scrollregion()
        if unmapped:
            # The canvas has no geometry yet (fresh window not mapped, e.g.
            # during a session switch on Windows).  Keep the follow armed:
            # the <Configure> hook re-pins as soon as real geometry lands,
            # instead of silently dropping the pin on the floor.
            self._thread_needs_pin = True
            return
        # The canvas window may already have grown while its new child is
        # still unmapped. Its bbox alone is not proof of visible content.
        # Follow only as far as mapped bubbles; Configure/settle callbacks
        # finish the follow once the new bubble has actual geometry.
        mapped_bottom = None
        for bubble in reversed(self.thread.winfo_children()):
            if not bubble.winfo_ismapped():
                continue
            padding = bubble.pack_info().get('pady', 0)
            if isinstance(padding, (tuple, list)):
                padding = padding[-1]
            mapped_bottom = (bubble.winfo_y() + bubble.winfo_height()
                             + bubble.winfo_pixels(padding))
            break
        if mapped_bottom is None:
            return                      # no mapped messages to follow yet
        bottom = min(self._content_bottom(), mapped_bottom)
        target_top = max(0, bottom - canvas.winfo_height())
        gap = target_top - self._viewport_top(canvas)
        if gap != 0:
            # Correct in BOTH directions. A previous layout may have put
            # the viewport beyond the currently mapped bubbles; merely
            # refusing to scroll farther down leaves that blank view intact.
            # Close the *measured* gap with unit scrolls (yscrollincrement
            # was set to 1 px).  yview_moveto(1.0) must never be used here:
            # Tk quantises the fraction against the scrollregion ratio, and
            # under Windows DPI scaling that rounding leaves a fixed few-px
            # residual gap at the bottom even though _thread_at_bottom()'s
            # epsilon already reports "pinned".
            try:
                canvas.yview_scroll(int(round(gap)), 'units')
            except Exception:
                self._manual_scroll_to_bottom(canvas)   # legacy fallback
            self._note_auto_move()

    def _schedule_thread_pin(self):
        """Collapse all pin requests in one event-loop cycle into one idle callback."""
        if self._pin_job is not None:
            return
        # A manual gesture observed since this era began outranks any pending
        # auto-pin: the view must stay exactly where the user put it until the
        # next rebuild arms a fresh window.
        if self._is_superseded_by_manual_scroll():
            self._thread_needs_pin = False
            return
        try:
            self._pin_job = self.after_idle(self._apply_thread_pin)
        except Exception:
            pass

    def _apply_thread_pin(self):
        self._pin_job = None
        self._thread_needs_pin = False
        if not self.winfo_exists() or not self._stick_to_bottom:
            return
        # Runs at idle time, right after Tk's geometry pass laid out the
        # freshly packed bubbles — the first moment every extent we measure
        # describes the *current* thread.  The settle poll and <Configure>
        # hook keep re-pinning while async bubble re-wraps land afterwards.
        # A manual scroll request observed since this pin was armed wins:
        # auto-scroll only ever follows *new* content, never the user.
        if self._is_superseded_by_manual_scroll():
            self._stick_to_bottom = False
            return
        self._scroll_thread_to_bottom()

    def _mint_manual_scroll_id(self):
        """Invalidate verdicts from older input and timestamp pointer evidence."""
        self._manual_scroll_seq += 1
        self._last_gesture_ms = time.monotonic()
        return self._manual_scroll_seq

    def _is_superseded_by_manual_scroll(self):
        """True when a genuine user scroll landed after the current pin era began.

        Automatic writes are tagged with ``_auto_scroll_depth`` inside the
        wrapped ``yview`` command and never bump the sequence counter, so any
        request above the era baseline can only come from the user (wheel over
        the thread, scrollbar thumb drag, trackbar click).  Once one exists,
        no previously scheduled pin/settle pass may move the view again —
        auto-scroll only ever follows *new* content.
        """
        return self._manual_scroll_seq > self._manual_scroll_baseline

    def _reset_manual_scroll_baseline(self):
        """Start a new stick-to-bottom era (rebuild or re-engaged pin).

        Any gesture that happened *before* this point relates to the previous
        layout; the rebuild below legitimately replaces the viewport target,
        so those older requests must not veto the fresh pins.
        """
        self._manual_scroll_baseline = self._manual_scroll_seq

    def _release_pin_for_manual_scroll(self):
        """Hand the viewport to the user: unstick and cancel pending auto jobs.

        Also re-anchors the veto baseline at the current sequence: this call
        always runs from a *fresh* user gesture, so any older gesture id must
        not keep vetoing pins armed after this point (otherwise stickiness
        could never re-engage once the user had scrolled even once).  The
        gesture's own id was minted before this call, so it still outranks
        anything scheduled earlier in the same instant.
        """
        self._stick_to_bottom = False
        self._reset_manual_scroll_baseline()
        self._thread_needs_pin = False
        self._stop_settle_poll()
        if self._pin_job is not None:
            try:
                self.after_cancel(self._pin_job)
            except Exception:
                pass
            self._pin_job = None
        # The settle poll used to be what kept the canvas scrollregion in
        # sync with the content (CTk only rewrites it from a deferred
        # <Configure> script).  Now that auto passes stop here, refresh the
        # region once at handover so the *user's* next wheel notch or thumb
        # drag clamps against fresh geometry instead of a stale region —
        # otherwise manual scrolling glitches (jumps/short-stops) until some
        # later event happens to fix the numbers.
        self._sync_scrollregion()

    def _note_auto_move(self):
        """Record one automatic viewport write (instrumentation for tests)."""
        self._auto_move_count = getattr(self, '_auto_move_count', 0) + 1

    def _start_settle_poll(self):
        self._stop_settle_poll()
        self._last_thread_height = -1
        self._settle_ticks = 0
        now = time.monotonic()
        # Hard ceiling: the poll can never outlive this, even if geometry
        # keeps churning.  The quiet window below normally ends it far
        # earlier; the ceiling bounds pathological resize loops.
        self._settle_deadline = now + (SETTLE_MAX_TICKS * SETTLE_POLL_MS) / 1000.0
        self._last_extent_change = now
        try:
            self._settle_job = self.after(SETTLE_POLL_MS, self._settle_tick)
        except Exception:
            pass

    def _stop_settle_poll(self):
        if self._settle_job is not None:
            try:
                self.after_cancel(self._settle_job)
            except Exception:
                pass
            self._settle_job = None

    def _settle_tick(self):
        self._settle_job = None
        if not self.winfo_exists() or not self._stick_to_bottom:
            return                          # user took over, or screen is gone
        canvas = self._thread_canvas
        if canvas is None or not canvas.winfo_exists():
            return
        if self._is_superseded_by_manual_scroll():
            # The user scrolled during the settle window: stop following the
            # layout and leave the viewport exactly where they put it.
            self._release_pin_for_manual_scroll()
            return
        self._settle_ticks += 1
        now = time.monotonic()
        hard_stop = now >= self._settle_deadline
        quiet_ms = (now - self._last_extent_change) * 1000.0
        self._sync_scrollregion()
        extent = self._content_bottom()
        gap = self._bottom_gap(canvas)
        changed = extent != self._last_thread_height
        if changed:
            # Bubbles still growing/shrinking (async text re-wrap) or the
            # deferred geometry pass finally landed: restart the quiet
            # window so late Windows bursts keep the poll alive instead of
            # the tick budget exiting right before they arrive.
            self._last_thread_height = extent
            self._last_extent_change = now
            quiet_ms = 0.0
        if hard_stop:
            # Last correction against whatever geometry exists now; then
            # stop no matter what (bounds pathological churn).
            self._scroll_thread_to_bottom()
            return
        if changed or (gap is not None and gap > BOTTOM_EPSILON_PX):
            # Still settling (extent moved) or the viewport drifted below
            # the pinned position: re-pin and keep watching.
            self._scroll_thread_to_bottom()
            self._settle_job = self.after(SETTLE_POLL_MS, self._settle_tick)
        elif gap is None and canvas.winfo_height() <= 1 \
                and quiet_ms < SETTLE_QUIET_MS:
            # Canvas not mapped yet — nothing measurable; stay armed until
            # geometry lands (or the quiet window runs out).
            self._thread_needs_pin = True
            self._settle_job = self.after(SETTLE_POLL_MS, self._settle_tick)
        elif quiet_ms < SETTLE_QUIET_MS:
            # Extent looks stable, but it has not been quiet long enough:
            # CTk's deferred scrollregion rewrite can land after a few
            # "stable" polls, so keep re-pinning through the grace window.
            self._scroll_thread_to_bottom()
            self._settle_job = self.after(SETTLE_POLL_MS, self._settle_tick)
        else:
            # Settled — polling stops entirely.  From here on *nothing* moves
            # the view automatically until the next refresh() arms a new
            # window, which is what keeps manual scrolling free outside of
            # content changes.
            self._stop_settle_poll()

    def _on_thread_configure(self, event=None):
        # Reactive hook for growth *inside* the settle window opened by
        # refresh() (async bubble re-wraps land as Configure bursts after the
        # rebuild).  Deliberately ignores everything once the window has
        # closed: ordinary layout churn — window resizes, hover effects,
        # suggestion popups packing into the thread — must never drag the
        # viewport back down while the user is browsing history.  Sticking
        # resumes only via a fresh rebuild (refresh) or an explicit manual
        # return to the bottom (_check_bottom_after_scroll).
        if self._settle_job is None:
            return
        if time.monotonic() >= self._settle_deadline:
            return                      # hard ceiling reached: window closed
        if self._stick_to_bottom and not self._thread_needs_pin \
                and not self._is_superseded_by_manual_scroll():
            self._schedule_thread_pin()

    def _observe_thread_wheel(self, event):
        widget = event.widget
        while widget is not None:
            if widget is self.thread or widget is self._thread_canvas or widget is self.thread._scrollbar:
                self._last_gesture_ms = time.monotonic()
                self._on_thread_wheel_event(event)
                return
            # Text fields own their wheel; do not release the chat pin for
            # scrolling a nested editor instead of the conversation.
            if isinstance(widget, ctk.CTkTextbox):
                return
            widget = getattr(widget, 'master', None)

    def _on_thread_wheel_event(self, event=None):
        # Wheel motion over the thread hands control to the user at once,
        # *before* CTk's bind_all handler scrolls the canvas (widget bindings
        # run first, so this also prevents our yview wrapper from mis-reading
        # the resulting scroll as an automatic write).  Whether stickiness
        # resumes is decided from the settled position by
        # _check_bottom_after_scroll (invoked from the yview wrapper for
        # downward gestures). The window observer filters by ancestry, so
        # wheel events in other panes never change the conversation pin.
        if not self.winfo_exists():
            return
        # Release before CTk moves the canvas. The actual yview write may
        # mint a newer id; its post-scroll verdict then supersedes this one.
        seq = self._mint_manual_scroll_id()
        self._release_pin_for_manual_scroll()
        # Up = negative units for Linux Button-4/5; Windows/mac delta is
        # positive when scrolling up.
        direction_up = True
        try:
            if event.num in (4, 5):
                direction_up = event.num == 4
            else:
                direction_up = getattr(event, 'delta', 0) > 0
        except Exception:
            direction_up = True
        if not direction_up:
            self.after_idle(lambda: self._check_bottom_after_scroll(seq))

    def _check_bottom_after_scroll(self, seq=None):
        # Deferred verdict for a downward manual gesture: if the settled
        # viewport really is back at the bottom, resume sticking; otherwise
        # stay released.  ``seq`` ties the verdict to one specific request —
        # a newer gesture supersedes older verdicts so a fast scroll burst
        # can never flip stickiness based on a stale position.
        if not self.winfo_exists():
            return
        if seq is not None and seq != self._manual_scroll_seq:
            return
        dbg = getattr(self, '_dbg', None)
        at_bottom = self._thread_at_bottom()
        if dbg is not None:
            dbg.append(('verdict', seq, at_bottom,
                        self._viewport_top(), self._content_bottom(),
                        self._thread_canvas.winfo_height()))
        if at_bottom:
            self._stick_to_bottom = True
            # New era: gestures up to and including this one belong to the
            # user's return-to-bottom, not to any pending auto pass.  Only
            # *future* scrolls can veto the pins armed below.
            self._reset_manual_scroll_baseline()
            self._thread_needs_pin = True
            self._schedule_thread_pin()
            self._start_settle_poll()

    def shutdown(self):
        # Cancel every pending auto-scroll job before teardown; worker-
        # posted callbacks must never fire against destroyed widgets.
        self._stop_settle_poll()
        if self._pin_job is not None:
            try:
                self.after_cancel(self._pin_job)
            except Exception:
                pass
            self._pin_job = None
        for top, sequence, token in self._wheel_bindings:
            try:
                top.unbind(sequence, token)
            except Exception:
                pass
        self._wheel_bindings.clear()
        self._hide_suggestions()
        for sid in {self.session_id, *(key[0] for key in self.service.assessments)}:
            self.service.cancel_turn(sid)
        self.events.stop()
