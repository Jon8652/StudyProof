"""Thin coaching UI; workers only generate, main thread commits."""
import threading
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
        self._stick_to_bottom = True    # False only while the user browses history
        self._pin_job = None            # pending after_idle pin callback
        self._settle_job = None         # periodic settle-poll after a rebuild
        self._settle_ticks = 0
        self._last_thread_height = -1   # height seen at previous settle poll
        self._thread_needs_pin = False  # set by refresh(); consumed at idle time
        self._thread_canvas = None
        self._in_sync_region = False    # re-entrancy guard for the sync drain
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
            # Any wheel motion over the thread decides stickiness from the
            # *real* post-scroll position (see _on_thread_wheel).  The
            # bind_all observer is scoped by Enter/Leave so sibling panes
            # never lose their wheel events to this screen.
            canvas.bind('<Enter>', lambda e: canvas.bind_all(
                '<MouseWheel>', self._on_thread_wheel_event, add='+'), add='+')
            canvas.bind('<Leave>', lambda e: canvas.unbind_all('<MouseWheel>'), add='+')
            for seq, sign in (('<Button-4>', 1), ('<Button-5>', -1)):   # Linux/Xvfb wheels
                canvas.bind(seq, lambda e, s=sign: self._on_thread_wheel(), add='+')
            # Dragging the CTk scrollbar thumb routes through the canvas
            # yview command as well, so a manual drag-up releases the pin.
            orig_yview = canvas.yview

            def tracked_yview(*args, **kw):
                result = orig_yview(*args, **kw)
                if (args and args[0] == 'scroll' and len(args) >= 3
                        and int(args[1]) < 0):
                    self._user_left_bottom_hint()
                return result
            canvas.yview = tracked_yview
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
                self.request('question')
            else:
                self.notice.configure(text=result.message)

    def sessions(self):
        if self.busy:
            self.cancel(cancel_assessments=False)
        dialog = SessionsDialog(self, self.state, self.service)
        self.wait_window(dialog)
        if dialog.result:
            self.resume_session(dialog.result)
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
            self.refresh()
        else:
            self.notice.configure(text=result.message)

    def resume_session(self, sid):
        self.cancel(cancel_assessments=False)
        self.session_id = sid
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
        self.refresh()

    def cancel(self, cancel_assessments=True):
        self.service.cancel_turn(self.session_id, cancel_assessments=cancel_assessments)
        self.busy = False
        self.notice.configure(text='Turn cancelled.')
        self.refresh()

    def _submitted(self, result):
        self.notice.configure(text=result.message or '')
        if result.ok:
            self.composer.delete('1.0', 'end')
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
        # Dismiss any visible tooltip before re-laying out the thread; stale
        # popups must never survive a state change or a moved pointer.  The
        # shared popup window is only hidden (never destroyed) here, which is
        # what keeps rapid refresh/hover churn from crashing Tk on Windows.
        Tooltip.dismiss_all()
        for child in self.thread.winfo_children():
            child.destroy()
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
        # --- Auto-scroll arming (single choke point) ---------------------
        # Every state change that rebuilds the thread funnels through here:
        # user send (_submitted), coach reply (finished), session start /
        # resume / switch (resume_session, sessions, revise_topic), cancel
        # and background assessment completion.  Arming at the end of
        # refresh() therefore covers all current and future call paths with
        # no per-callsite hooks to forget.
        if session is not None:
            # A (re)built thread ALWAYS lands at its bottom — absolute
            # pinning, so switching to a shorter session scrolls *up* if
            # the old view was parked mid-scroll; direction never matters.
            self._stick_to_bottom = True
            self._thread_needs_pin = True
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
        else:
            self._stop_settle_poll()

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

        Never ``canvasy(0)`` or raw ``yview()`` fractions alone: they are
        computed by Tk against the canvas's *current* scrollregion, which
        lags a fresh rebuild by an event cycle and reports positions derived
        from the old content — the exact source of the spurious "already at
        bottom" short-circuits.  CTk mirrors every scroll into its custom
        scrollbar (``yscrollcommand=self._scrollbar.set`` stores the same
        fractions synchronously), so cross-checking the two sources gives a
        trustworthy position: if the mirrored scrollbar disagrees with what
        ``canvasy(0)`` implies, the region is stale and the fractional read
        is meaningless — fall back to the settled extent instead.
        """
        canvas = canvas if canvas is not None else self._thread_canvas
        if canvas is None or not canvas.winfo_exists():
            return 0.0
        try:
            height = canvas.winfo_height()
            scrollbar = getattr(self.thread, '_scrollbar', None)
            start, _end = canvas.yview()
            if scrollbar is not None:
                mirror = getattr(scrollbar, 'get', lambda: (None, None))()
                if (mirror and mirror[0] is not None
                        and abs(mirror[0] - start) > 0.02):
                    # Scrollbar callback hasn't fired for this region yet
                    # (or the region shrank under us): yview() is describing
                    # the previous thread.  Treat the view as parked at the
                    # settled content bottom — the pin recomputes the true
                    # offset right after, so this only prevents false
                    # "at bottom" verdicts between rebuild passes.
                    return self._content_bottom() - height
            if height > 1:
                region = canvas.bbox('all')
                if region is not None and region[3] - region[1] > 0:
                    implied = (start * (region[3] - region[1]))
                    if abs(canvas.canvasy(0) - implied) > max(4.0, height * 0.05):
                        return self._content_bottom() - height
            return float(canvas.canvasy(0))
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
        """
        canvas = self._thread_canvas
        if canvas is None or not canvas.winfo_exists():
            return
        try:
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
                self._sync_scrollregion()
                return
            # Fraction such that the viewport's bottom edge sits exactly on
            # the estimated content bottom — independent of how stale the
            # current scrollregion is.
            canvas.yview_moveto(max(0.0, min(1.0, (bottom - height) / span)))
            self._sync_scrollregion()
        except Exception:
            try:
                self._manual_scroll_to_bottom(canvas)   # legacy fallback
            except Exception:
                pass

    def _schedule_thread_pin(self):
        """Collapse all pin requests in one event-loop cycle into one idle callback."""
        if self._pin_job is not None:
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
        self._scroll_thread_to_bottom()

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
        elif self._settle_ticks < SETTLE_MAX_TICKS:
            # Stable and pinned, but Tk may still hold queued Configure
            # events (e.g. a wraplength burst). One extra quiet tick, then stop.
            self._settle_job = self.after(SETTLE_POLL_MS, self._settle_tick)
        # else: settled — polling stops entirely (zero idle cost between messages)

    def _on_thread_configure(self, event=None):
        # Reactive hook for growth *after* the settle window (rare, e.g. a
        # resize re-wrap while stuck at bottom).  The short-circuit inside
        # _scroll_thread_to_bottom keeps this bounded: pinning re-fires the
        # frame's <Configure>, which schedules one redundant no-op at most.
        if self._stick_to_bottom and not self._thread_needs_pin:
            self._schedule_thread_pin()

    def _on_thread_wheel_event(self, event=None):
        # bind_all passes the wheel event; only decide from it while the
        # pointer is actually over the thread (Enter/Leave-scoped binding).
        self._on_thread_wheel()

    def _on_thread_wheel(self, direction_or_event=None):
        """Wheel motion decides stickiness from the post-scroll position."""
        if self._thread_at_bottom():
            self._stick_to_bottom = True
        else:
            self._stick_to_bottom = False

    def _user_left_bottom_hint(self):
        # Called from upward scrollbar drags; decide from the settled position.
        try:
            self.after_idle(self._check_bottom_after_scroll)
        except Exception:
            pass

    def _check_bottom_after_scroll(self):
        if not self.winfo_exists():
            return
        if not self._thread_at_bottom():
            self._stick_to_bottom = False

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
