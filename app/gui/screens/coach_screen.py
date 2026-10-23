"""Thin coaching UI; workers only generate, main thread commits."""
import threading
import time

import customtkinter as ctk
from app.core.agent.coach_service import CoachService
from app.core.agent.grounding import PageIndex
from app.core.agent.provider_openai import sanitize_base_url
from app.core.agent.provider_base import ProviderError
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
        self.header = ctk.CTkLabel(self.content, text='', wraplength=MIN_WRAPLENGTH, justify='left')
        self.header.pack(fill='x')
        # Pane 2: the chat thread.  Unstyled CTkScrollableFrame used to show
        # customtkinter's default grey; now the same blue pane surface.
        self.thread = ctk.CTkScrollableFrame(self.content, fg_color=PANE_BG)
        self.thread.pack(fill='both', expand=True)
        # --- Auto-scroll ("stick to bottom") state ----------------------
        # Mirrors how mainstream chat clients behave: the newest message is
        # always kept in view unless the user deliberately scrolls away to
        # read history.  All values are main-thread only; nothing touches Tk
        # from a worker (turn results already arrive via UIEventQueue).
        # User agency preserved: auto-scroll fires ONLY in the bounded
        # "settle window" opened by a *conversation-event* rebuild —
        # opening/resuming/switching a session, a message the user sent, or a
        # new coach reply (see _pending_follow_event below).  Button clicks
        # and other plain re-renders open no window at all, and outside any
        # window nothing ever moves the view on a timer, so ordinary layout
        # churn (window resizes, hover effects, suggestion popups) can never
        # fight a manual scroll.  Manual input
        # is detected on two channels:
        #   * wheel events over the thread (scoped via Enter/Leave), and
        #   * the canvas ``yview`` command, which CTk routes *every* scroll
        #     request through — CTk's own bind_all wheel handler AND the
        #     scrollbar thumb/clicks.  Tagging each request with a sequence
        #     number lets any auto pass veto itself when a newer user gesture
        #     landed since it was armed (see _is_superseded_by_manual_scroll).
        # Once the user scrolls away, sticking stays off until they return to
        # the bottom or the next rebuild arms a fresh window.
        self._stick_to_bottom = True    # False only while the user browses history
        # --- Gesture evidence window --------------------------------------
        # ``tracked_yview`` below is the one observer of EVERY scroll request,
        # but Tk itself emits yview writes that no user asked for: whenever a
        # widget inside the canvas is destroyed/repacked and the content no
        # longer fits the *current* scrollregion, Tcl clamps the viewport by
        # calling the very same ``yview`` command.  Those internal clamp-writes
        # are indistinguishable from a real gesture by arguments alone — yet a
        # genuine wheel notch / thumb drag / trackbar click ALWAYS runs a
        # pointer binding first (canvas <Enter>-scoped MouseWheel/Button-4/5
        # observers, or CTk's scrollbar callbacks).  So manual classification
        # requires fresh gesture evidence: a pointer event seen within
        # GESTURE_EVIDENCE_MS of the yview request.  Writes without evidence
        # (dialog-close relayouts, geometry churn, CTk scrollbar internals)
        # never bump the sequence counter, never release the pin and never
        # veto an armed follow — which is exactly what used to produce the
        # "jump toward the bottom then snap back" flicker after clicking
        # "Sessions…" / "Revise Weak Topics" mid-manual-scroll.
        self._last_gesture_ms = 0       # last time a real pointer gesture was observed
        # True while the thread's children are being destroyed/repacked in
        # refresh().  Tk clamps the viewport through the canvas ``yview``
        # command during that window (the scrollregion shrinks with the old
        # bubbles gone); those writes carry no pointer evidence and must be
        # inert — see tracked_yview below.
        self._rebuilding = False
        # --- Event-driven arming (the ONLY legitimate auto-scroll trigger) -
        # Auto-scroll follows *events*, never rendering: opening/resuming/
        # switching to a session, a message the user just sent, and a new
        # reply (or cancel notice) from the coach.  Those code paths set
        # ``_pending_follow_event`` explicitly right before calling
        # refresh(); every other caller — button handlers like "Sessions…"
        # or "Revise Weak Topics", background assessment completion, gate
        # changes, redundant re-renders — leaves the flag unset, so their
        # rebuild is completely inert: zero pin passes, zero scrollregion
        # syncs, zero viewport movement or blank-flash glitch.  A heuristic
        # content-diff once tried to *infer* which rebuilds were "new";
        # it mis-classified button churn as content and kept glitching, so
        # inference is gone entirely — the events declare themselves.
        self._pending_follow_event = False
        self._pin_job = None            # pending after_idle pin callback
        self._settle_job = None         # periodic settle-poll after a rebuild
        self._settle_ticks = 0
        self._last_thread_height = -1   # height seen at previous settle poll
        self._thread_needs_pin = False  # set by refresh(); consumed at idle time
        self._thread_canvas = None
        self._in_sync_region = False    # re-entrancy guard for the sync drain
        self._manual_scroll_seq = 0     # id of the newest scroll request seen
        self._auto_scroll_depth = 0     # >0 while an automatic pin is executing
        # Sequence number the current stick-to-bottom era began at.  Only
        # requests *after* this line are user gestures; earlier ones were part
        # of (or before) the automatic pass that established the pin.
        self._manual_scroll_baseline = 0
        self._gesture_seq = 0         # id of the newest *user* gesture
        # Number of automatic viewport writes performed since the last era
        # reset — instrumentation only (tests assert no stray auto-movement
        # during inert re-renders); never read by the scroll logic itself.
        self._auto_move_count = 0
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
            # Observe wheel motion over the thread so the pointer-local
            # gesture releases the pin *before* CTk's bind_all handler moves
            # the canvas (bind_all callbacks run after widget-bindings, so
            # doing it here is what keeps our own yview wrapper from
            # mis-reading the resulting scroll as an automatic write).
            # The bind_all observer is Enter/Leave-scoped so sibling panes
            # never lose their wheel events to this screen.
            canvas.bind('<Enter>', lambda e: canvas.bind_all(
                '<MouseWheel>', self._on_thread_wheel_event, add='+'), add='+')
            canvas.bind('<Leave>', lambda e: canvas.unbind_all('<MouseWheel>'), add='+')
            for button_seq in ('<Button-4>', '<Button-5>'):   # Linux/Xvfb wheels
                canvas.bind(button_seq, self._on_thread_wheel_event, add='+')
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
                    # Gesture-evidence gate: a yview WRITE counts as a user
                    # gesture only if a pointer event (wheel observer,
                    # scrollbar press/drag, CTk's wheel handler) fired within
                    # GESTURE_EVIDENCE_MS.  Without that evidence the write is
                    # Tk's own clamp against a stale scrollregion — during a
                    # rebuild (destroy-and-repack) those must stay completely
                    # inert: no sequence bump, no pin release, no veto of an
                    # armed follow.  That false veto was the whole "jump
                    # toward the bottom then snap back" flicker after closing
                    # the Sessions / Revise-Weak-Topics dialogs mid-scroll.
                    # Outside a rebuild, untagged writes can only come from a
                    # genuine interaction (or its immediate aftermath), so
                    # they still count — legacy paths that call the raw
                    # command stay covered.
                    evidence = (time.monotonic() - self._last_gesture_ms) * 1000.0 \
                        <= GESTURE_EVIDENCE_MS
                    # A genuine interaction always stamps evidence first:
                    # wheel notches hit our canvas binding / CTk's wrapped
                    # ``_mouse_wheel_all``, and thumb drags / trackbar clicks
                    # route through the scrollbar's repointed command shim
                    # before reaching this wrapper.  Tk's own clamp writes
                    # (scrollregion rewrites against stale viewport bounds)
                    # never touch those channels, so untagged writes outside
                    # a rebuild window can only be legacy callers using the
                    # raw command — honour them to keep every real gesture
                    # vetoing an armed follow.
                    manual = evidence or not self._rebuilding
                if manual:
                    # The wheel observer (<Button-4/5>/<MouseWheel> canvas
                    # bindings + _on_thread_wheel_event) runs *before* CTk's
                    # bind_all handler reaches this wrapper and claims each
                    # notch by minting a fresh id and stamping _gesture_seq
                    # to it.  When that claim is still current — same id,
                    # fresh evidence, pin already released — this write is
                    # just the visible motion of the notch the observer
                    # already accounted for: stay completely inert here (no
                    # second bump, which would orphan the observer's
                    # deferred bottom-verdict and let a spurious re-stick
                    # yank the view back down mid-burst).  Writes with no
                    # live claim (thumb drags, trackbar clicks, legacy raw
                    # -command callers) are owned by this wrapper instead.
                    claimed = (evidence
                               and self._gesture_seq == self._manual_scroll_seq
                               and not self._stick_to_bottom)
                    if not claimed:
                        seq = self._mint_manual_scroll_id()
                        direction_up = True
                        if str(args[0]).lower() == 'scroll':
                            try:
                                direction_up = int(args[1]) < 0   # CTk sign: up = negative
                            except (ValueError, TypeError):
                                direction_up = True
                        else:
                            try:
                                start = canvas.yview()[0]
                                direction_up = float(args[1]) < start - 1e-9
                            except Exception:
                                direction_up = True
                        # Hand control over *before* the scroll executes: CTk's
                        # scrollbar wheel handler calls this command without any
                        # separate wheel observer, so releasing first is what
                        # keeps the resulting motion from being vetoed away.
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
        # customtkinter binds its wheel handler (``_mouse_wheel_all``) via
        # ``bind_all``, which registers on every widget of the class — the
        # parent canvas included.  A wheel notch therefore runs the canvas's
        # own binding *first*: our Enter/Leave-scoped ``bind_all`` observer
        # only fires afterwards (and the instance-level patch below only
        # covers the frame), so without a direct canvas binding an upward
        # gesture would execute before the pin was released and a pending
        # settle pass could yank the view back down.  Bind the release hook
        # on the canvas itself for all wheel event flavours; harmless
        # duplicates when several channels fire for one notch, since the
        # release is idempotent.
        if canvas is not None:
            for wheel_seq in ('<MouseWheel>', '<Button-4>', '<Button-5>'):
                canvas.bind(wheel_seq, self._on_thread_wheel_event, add='+')
        _orig_wheel_all = self.thread._mouse_wheel_all

        def _tracked_mouse_wheel_all(event):
            try:
                over_us = event.widget is canvas or (
                    self.thread.winfo_containing(event.x_root, event.y_root)
                    in (canvas, self.thread))
            except Exception:
                over_us = False
            if over_us:
                # Stamp gesture evidence even when the pin is already
                # released, so the scroll CTk's handler is about to perform
                # is classified as a user gesture by tracked_yview.
                self._last_gesture_ms = time.monotonic()
            if over_us and self._auto_scroll_depth == 0 \
                    and self.winfo_exists():
                # Release unconditionally (not only while stuck): a notch may
                # arrive after the pin was already released, and its scroll
                # still needs the veto id bumped *before* it executes.  The
                # release is idempotent; _on_thread_wheel_event keeps exactly
                # one bump per notch via its was_stuck guard.
                self._release_pin_for_manual_scroll()
            return _orig_wheel_all(event)
        self.thread._mouse_wheel_all = _tracked_mouse_wheel_all
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
            self.notice.configure(text='Answer in your own words, or ask for help with this topic.')
            # Opening a new session is a conversation event: land at its
            # newest message (absolute pin — direction never matters).
            self._pending_follow_event = True
            self.refresh()
        else:
            self.notice.configure(text=result.message)

    def resume_session(self, sid):
        self.cancel(cancel_assessments=False)
        self.session_id = sid
        # Switching sessions is a conversation event: always open the view at
        # its newest message, whether reaching it means scrolling up or down.
        self._pending_follow_event = True
        self.refresh()

    def request(self, kind):
        if not self._require_connection():
            return
        self._last_action = kind
        session = self.state.coach_session_by_id(self.session_id)
        prepared = self.service.prepare_turn(
            self.session_id, kind, conversational=bool(session and session.turns))
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
        self.notice.configure(text=result.message or self._assessment_notices.pop(job['session_id'], ''))
        # A new coach reply just landed in the thread — a conversation event.
        self._pending_follow_event = True
        self.refresh()

    def cancel(self, cancel_assessments=True):
        was_busy = self.busy
        self.service.cancel_turn(self.session_id, cancel_assessments=cancel_assessments)
        self.busy = False
        self.notice.configure(text='Turn cancelled.')
        # Only an actual cancellation appends a "turn cancelled" notice to
        # the thread; the button-press paths call cancel() defensively while
        # nothing is in flight, which must NOT move the viewport.
        if was_busy:
            self._pending_follow_event = True
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
        q = session.questions[-1] if session and session.questions else None
        text = self.composer.get('1.0', 'end').strip()
        if not q or q.status == 'closed':
            self.notice.configure(text='Choose New session and select your notes or PDFs to get a question first.')
            return 'break'
        if not text:
            if q.first_answer is not None and not q.feedback_given:
                self.request('feedback')
            return 'break'
        result = self.service.submit_message(self.session_id, text)
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
        q = session.questions[-1] if session and session.questions else None
        if q and q.first_answer is None and not self.busy and self._suggestion_job is None:
            self._suggestion_job = self.after(15_000, self._show_suggestions)

    def _show_suggestions(self):
        self._suggestion_job = None
        session = self.state.coach_session_by_id(self.session_id)
        q = session.questions[-1] if session and session.questions else None
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
        q = session.questions[-1] if session and session.questions else None
        # --- Auto-scroll decision (BEFORE any teardown of the old view) ---
        # Arming is purely event-driven: the conversation events that add a
        # newest message — opening/resuming/switching a session, sending one,
        # or receiving a coach reply/cancel notice — set
        # ``_pending_follow_event`` right before calling us.  Every other
        # caller ("Sessions…" closing without switching, the weak-topics
        # picker, background assessment completion, gate-only rebuilds,
        # redundant re-renders) leaves it unset, so this rebuild is
        # completely inert: no pin passes, no settle poll, no scrollregion
        # syncs — zero viewport movement and no blank-flash glitch while the
        # bubbles repack.  The flag is consumed here regardless of outcome so
        # a later button-triggered refresh can never inherit an old arming.
        follow_event = self._pending_follow_event
        self._pending_follow_event = False
        dbg = getattr(self, '_dbg', None)
        if dbg is not None:
            dbg.append(('refresh', follow_event, 'seq', self._manual_scroll_seq,
                        'base', self._manual_scroll_baseline,
                        'stick', self._stick_to_bottom))
        if not follow_event:
            # Pure re-render: cancel any auto work still in flight so nothing
            # moves while the thread rebuilds.
            self._stop_settle_poll()
        # Dismiss any visible tooltip before re-laying out the thread; stale
        # popups must never survive a state change or a moved pointer.  The
        # shared popup window is only hidden (never destroyed) here, which is
        # what keeps rapid refresh/hover churn from crashing Tk on Windows.
        Tooltip.dismiss_all()
        # Mark the destroy-and-repack window: while it is open, any yview
        # write without fresh pointer evidence is Tk clamping against the
        # collapsing scrollregion and must not be mistaken for a gesture
        # (tracked_yview).  Cleared at idle, once every queued Configure /
        # clamp-write from this teardown has been processed.
        self._rebuilding = True
        for child in self.thread.winfo_children():
            child.destroy()
        try:
            self.after_idle(lambda: setattr(self, '_rebuilding', False))
        except Exception:
            self._rebuilding = False
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
            self.header.configure(text=f'{mode.title()} · ' + ' · '.join(labels))
            for turn in session.turns:
                if turn.kind == 'confidence':
                    continue
                ChatBubble(self.thread, turn, session.sources, index, self.navigate).pack(fill='x', pady=4)
        else:
            self.header.configure(text='Pick sources to start a coached session.')
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
            'question': bool(session) and (not q or q.feedback_given),
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
        # --- Auto-scroll arming (uses the decision taken at refresh() top) -
        # Only a declared conversation event may move the viewport; any
        # other rebuild leaves it exactly where it is (manual browsing
        # untouched, no pin passes, no settle poll, and crucially NO
        # scrollregion churn while Tk is still settling the freshly repacked
        # bubbles — a sync during that window clamps yview against a stale
        # bbox and produces the blank flash + snap-back glitch on clicks).
        if session is not None and follow_event:
            # New conversation content (a sent message, a coach reply, a
            # cancel notice) or opening/resuming/switching to a session.  It
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
        """Best immediate estimate (px) of where the thread's content ends.

        The canvas ``bbox('all')`` and the inner frame's allocated height are
        driven by deferred Configure/idle geometry passes, so right after a
        destroy-and-repack they still describe the *previous* thread — in
        BOTH directions: stale-high after switching to a shorter session
        (pinning there parks the view over blank space) and stale-low while
        bubbles re-wrap.  What updates synchronously with each ``pack()`` is
        the packer's requested size for the frame (grid ``reqheight``) plus
        the per-bubble ``winfo_reqheight`` values inside it.  Summing those
        gives the final layout height immediately after a rebuild, so take
        the maximum of all sources: it never under-estimates the true extent,
        and any transient over-estimate is corrected by the idle/settle pins
        once Tk's geometry catches up.
        """
        best = 0
        canvas = self._thread_canvas
        try:
            if canvas is not None and canvas.winfo_exists():
                bbox = canvas.bbox('all')
                if bbox is not None:
                    best = max(best, bbox[3])
        except Exception:
            pass
        try:
            if self.thread.winfo_exists():
                best = max(best, self.thread.winfo_height())
                kids = self.thread.winfo_children()
                if kids:
                    last = max(kids, key=lambda c: c.winfo_y() + c.winfo_height())
                    best = max(best, last.winfo_y() + last.winfo_height())
                    # Synchronous estimate: total requested height of the
                    # packed children plus the frame's own grid padding —
                    # exactly what the packer will allocate after its next
                    # idle pass, available right now.
                    total = sum(int(kid.winfo_reqheight()) for kid in kids)
                    try:
                        info = self.thread.grid_info()
                        ipady = int(info.get('ipady', 0) or 0)
                        pady_hi = int(str(info.get('pady', '0')).split()[1])
                    except Exception:
                        ipady = pady_hi = 0
                    best = max(best, total + 2 * ipady + 2 * pady_hi)
        except Exception:
            pass
        return best

    def _viewport_top(self, canvas=None):
        """Current top edge of the visible area, in canvas coordinates.

        ``canvasy(0)`` is Tk's own pixel truth for the yview origin — it
        always describes where the viewport actually is (against the current
        scrollregion).  The one unreliable case is a *stationary* viewport
        while the scrollregion grows underneath it (async bubble re-wraps
        lengthen the thread without any scroll request): Tk's fractions then
        still describe the pre-growth region and ``canvasy(0)`` reads too low.
        CTk mirrors every genuine scroll into its custom scrollbar
        synchronously, so when the mirrored window ends short of the *current*
        region there really is content below the fold — take the deeper of
        the two readings.  This must never fall back to raw content extents:
        after switching to a shorter session those still describe the old
        (taller) thread for an event cycle, and a stale-high read makes an
        upward manual gesture look like "still at the bottom", silently
        re-engaging stickiness and yanking the view back down.
        """
        canvas = canvas if canvas is not None else self._thread_canvas
        if canvas is None or not canvas.winfo_exists():
            return 0.0
        try:
            height = canvas.winfo_height()
            region = canvas.bbox('all')
            top = float(canvas.canvasy(0))
            if height > 1 and region is not None and region[3] - region[1] > 0:
                span = region[3] - region[1]
                scrollbar = getattr(self.thread, '_scrollbar', None)
                mirror = getattr(scrollbar, 'get', lambda: (None, None))()
                if mirror and mirror[1] is not None and mirror[1] < 0.995:
                    top = max(top, mirror[1] * span - height)
            return top
        except Exception:
            try:
                return float(canvas.canvasy(0))
            except Exception:
                return 0.0

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
        """Absolute pin — works whether reaching the bottom means scrolling up or down.

        Never trusts Tk's own clamping: right after a destroy-and-repack the
        canvas scrollregion still describes the *previous* thread (CTk only
        rewrites it from a deferred ``<Configure>`` script), so a
        ``yview_scroll`` past the end lands short after growth or parks over
        blank space after switching to a shorter session.  Instead, compute
        the exact top-edge offset from our synchronous content estimate and
        drive the view there directly with ``yview_moveto``, refreshing the
        scrollregion afterwards so the scrollbar geometry stays consistent.

        The whole pass runs inside ``_auto_scroll_depth`` so the wrapped
        ``yview`` command recognises its own writes as automatic instead of
        mis-reading them as user gestures, and bails out if a genuine gesture
        landed since this pass was armed.
        """
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

    def _pin_viewport(self, canvas):
        """Geometry half of the pin (see _scroll_thread_to_bottom)."""
        height = canvas.winfo_height()
        if height <= 1:
            # Un-mapped viewport (e.g. the screen has never been shown):
            # no meaningful fraction exists, so clamp against whatever
            # region we do have and let later passes refine.
            self._manual_scroll_to_bottom(canvas)
            return
        bottom = self._content_bottom()
        region = canvas.bbox('all')
        span = max((region[3] - region[1]) if region else 0, bottom)
        if bottom <= 0 or span <= height:
            # Empty thread or everything fits: yview at the top shows
            # all content; moveto(0) also survives a stale-high region.
            canvas.yview_moveto(0.0)
            self._note_auto_move()
            self._sync_scrollregion()
            return
        # Fraction such that the viewport's bottom edge sits exactly on
        # the estimated content bottom — independent of how stale the
        # current scrollregion is.
        target = max(0.0, min(1.0, (bottom - height) / span))
        if abs(canvas.canvasy(0) - (bottom - height)) <= BOTTOM_EPSILON_PX:
            # Pixel truth beats fractions here: after a scrollregion
            # rewrite Tk's own yview() values can lag one cycle behind
            # the real position, and a fraction-based "already pinned"
            # check would then short-circuit genuine re-pins while the
            # settle window is still open.  The viewport bottom already
            # covers the newest content — leave the view alone.
            return
        canvas.yview_moveto(target)
        self._note_auto_move()
        self._sync_scrollregion()

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
        """Record a fresh user gesture and return its veto id.

        Every manual scroll channel (wheel observer, yview wrapper for
        thumb drags / trackbar clicks) funnels through here so exactly one
        id is minted per gesture; ``_gesture_seq`` marks the newest id as
        *user-owned* so the yview wrapper can recognise — and stay inert
        for — the automatic follow-up write of a notch it already claimed.
        """
        self._manual_scroll_seq += 1
        self._gesture_seq = self._manual_scroll_seq
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
        self._sync_scrollregion()
        extent = self._content_bottom()
        if extent != self._last_thread_height:
            # Bubbles still growing/shrinking (async text re-wrap) or the
            # deferred geometry pass finally landed: re-pin against the new
            # extent and keep watching until it stabilises.
            self._last_thread_height = extent
            self._scroll_thread_to_bottom()
            self._settle_job = self.after(SETTLE_POLL_MS, self._settle_tick)
        elif not self._thread_at_bottom():
            # Height stable but the viewport drifted (late scrollregion
            # correction, DPI/geometry settle): re-pin and keep watching.
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
        if self._settle_job is None or self._settle_ticks >= SETTLE_MAX_TICKS:
            return
        if self._stick_to_bottom and not self._thread_needs_pin \
                and not self._is_superseded_by_manual_scroll():
            self._schedule_thread_pin()

    def _on_thread_wheel_event(self, event=None):
        # Wheel motion over the thread hands control to the user at once,
        # *before* CTk's bind_all handler scrolls the canvas (widget bindings
        # run first, so this also prevents our yview wrapper from mis-reading
        # the resulting scroll as an automatic write).  Whether stickiness
        # resumes is decided from the settled position by
        # _check_bottom_after_scroll (invoked from the yview wrapper for
        # downward gestures).  The bind_all observer is Enter/Leave-scoped,
        # so this only runs while the pointer is over the chat thread.
        if not self.winfo_exists():
            return
        # Claim this notch FIRST, unconditionally.  This handler runs
        # *before* any other wheel binding (widget bindings precede
        # bind_all), so the sequence bump + evidence stamp below become the
        # gesture's single canonical id: tracked_yview recognises CTk's
        # follow-up write as the same notch (already claimed) instead of
        # minting a competing id, and every auto-pin armed before this
        # moment is vetoed by it.  The claim happens even when the pin was
        # already released (mid-browse) — a bare yank-back guard must still
        # count as the newest gesture.
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
        try:
            self.unbind_all('<MouseWheel>')
        except Exception:
            pass
        self._hide_suggestions()
        for sid in {self.session_id, *(key[0] for key in self.service.assessments)}:
            self.service.cancel_turn(sid)
        self.events.stop()
