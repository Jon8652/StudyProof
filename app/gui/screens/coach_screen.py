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
        # --- Sticky-bottom auto-scroll state ------------------------------------
        # Chatbot-style behaviour: new coach replies and freshly sent messages
        # keep the view pinned to the bottom of the thread.  If the user has
        # scrolled up to read history, auto-scroll pauses until they return to
        # the bottom (wheel/scrollbar) or send a new message (which re-sticks).
        # When the newest bubble is taller than the viewport, the scroll target
        # prioritises showing the *start* of the latest user prompt instead of
        # blindly jumping to the absolute end.
        self._chat_canvas = getattr(self.thread, '_parent_canvas', None)
        self._stick_to_bottom = True
        self._autoscroll_job = None
        self._autoscroll_retries = 0
        if self._chat_canvas is not None:
            # Wheel events land on whatever widget sits under the pointer —
            # the canvas, the inner frame, or a bubble.  CTkScrollableFrame
            # already forwards wheel events from its own <Enter>/<Leave>
            # handlers, so instance bindings on the two containers plus a
            # class binding (dispatched *before* any widget-level handler)
            # cover every scroll gesture without touching customtkinter's
            # internal bindings — those use ``event.widget`` and would
            # crash if an add='+' binding ran first on a CTk widget.
            for target in (self._chat_canvas, self.thread):
                target.bind('<MouseWheel>', self._on_chat_scroll_input, add='+')
                target.bind('<Button-4>', self._on_chat_scroll_input, add='+')
                target.bind('<Button-5>', self._on_chat_scroll_input, add='+')
            try:
                self._chat_canvas.bind_class(
                    'Canvas', '<MouseWheel>', self._on_chat_scroll_input, add='+')
                self._chat_canvas.bind_class('Canvas', '<Button-4>',
                                             self._on_chat_scroll_input, add='+')
                self._chat_canvas.bind_class('Canvas', '<Button-5>',
                                             self._on_chat_scroll_input, add='+')
            except Exception:
                pass                      # class binding unavailable
            # Scrollbar thumb drags bypass MouseWheel entirely: CTkScrollbar
            # calls its ``command`` (the canvas yview) directly from
            # _on_motion.  Wrap that command so every drag re-syncs the
            # stick state against the *new* view.  The wrapper is installed
            # lazily on the first drag because customtkinter re-configures
            # the scrollbar's command during its own geometry handling —
            # wrapping in __init__ would be overwritten before any real
            # scroll happens.  ``_stick_synced`` guards against wrapping a
            # second time.
            try:
                scrollbar = getattr(self.thread, '_scrollbar', None)
                if scrollbar is not None:
                    def _wrap_drag_command(_sb=scrollbar):
                        try:
                            current = _sb.cget('command')
                            if current is None or getattr(current, '_stick_synced', False):
                                return
                            def _sync_stick(*args, _cmd=current):
                                _cmd(*args)       # let CTk update the view first
                                self._on_chat_scroll_input()
                            _sync_stick._stick_synced = True
                            _sb.configure(command=_sync_stick)
                        except Exception:
                            pass               # internals changed: wheel-only
                    self._on_chat_scroll_input_wrapped = _wrap_drag_command
                    for seq in ('<Button-1>', '<B1-Motion>'):
                        # CTkScrollbar.bind() forwards to its internal
                        # canvas with add=True, preserving its own handlers.
                        scrollbar.bind(seq, lambda e=None: _wrap_drag_command(), add='+')
            except Exception:
                pass                      # customtkinter internals changed
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
        if sid != self.session_id:
            # Opening a saved conversation starts at the top of the history,
            # not pinned to its end like a live chat would be.
            self._stick_to_bottom = False
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
        # A learner's own message always wins visibility priority: re-stick
        # even if they had been reading older history, and let the oversized
        # bubble logic show the *start* of their latest prompt first.
        self._schedule_thread_follow(force=result.ok)

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

    # ------------------------------------------------------------------
    # Sticky-bottom thread scrolling

    def _on_chat_scroll_input(self, event=None):
        """Wheel/drag inside the thread: re-stick when back at the bottom.

        Scrolling *up* is handled by the follow-up poll below (the yview
        fraction moves away from 1.0), so this handler only needs to catch
        the opposite direction — deliberately returning to the bottom
        resumes following new messages, like mainstream chat apps.
        """
        if event is not None and getattr(event, 'num', None) == 4:
            # X11 Button-4 scrolls up; leave the stick state alone here.
            return
        canvas = self._chat_canvas
        if canvas is None or not canvas.winfo_exists():
            return
        try:
            if canvas.yview()[1] >= 0.995:
                self._stick_to_bottom = True
        except Exception:
            pass                          # not yet realised / torn down

    def _last_user_bubble(self):
        """Most recent learner bubble in the thread, or None."""
        for child in reversed(self.thread.winfo_children()):
            if isinstance(child, ChatBubble) and getattr(child, 'role', None) == 'user':
                return child
        return None

    def _schedule_thread_follow(self, force=False):
        """Queue a post-layout scroll of the thread.

        ``force`` re-sticks unconditionally — used right after the learner
        sends a message so their own prompt always comes into view even if
        they had been reading older history.
        """
        if force:
            self._stick_to_bottom = True
        if self._autoscroll_job is not None:
            try:
                self.after_cancel(self._autoscroll_job)
            except Exception:
                pass                      # stale id after interpreter reset
        # One deferred pass lets Tk finish geometry management for the
        # freshly packed/destroyed bubbles before we measure anything.
        self._autoscroll_retries = 0
        self._autoscroll_job = self.after(0, self._follow_thread_bottom)

    def _follow_thread_bottom(self):
        self._autoscroll_job = None
        canvas = self._chat_canvas
        if canvas is None or not canvas.winfo_exists() or not self.thread.winfo_exists():
            return
        if not self._stick_to_bottom:
            # User is reading history: only keep an eye on manual scrolling
            # so that dragging/wheeling back down re-engages auto-follow.
            try:
                if canvas.yview()[1] >= 0.995:
                    self._stick_to_bottom = True
            except Exception:
                pass
            return
        target, settled = self._bottom_scroll_target(canvas)
        try:
            canvas.yview_moveto(target)
        except Exception:
            return                        # widget torn down mid-flight
        # CTkScrollableFrame recomputes its scrollregion on the inner
        # frame's <Configure>, which fires *after* the first idle pass;
        # labels also re-wrap asynchronously.  Retry a bounded number of
        # times until the geometry has settled, otherwise the view would
        # stop short of the true bottom after long replies.
        if not settled and self._autoscroll_retries < 6:
            self._autoscroll_retries += 1
            self._autoscroll_job = self.after(30, self._follow_thread_bottom)

    def _bottom_scroll_target(self, canvas):
        """Fraction to scroll to, plus whether the geometry looks settled.

        Normally this pins the very bottom of the thread.  Edge case: when
        the newest message is taller than the viewport, plain bottom-scroll
        shows only its tail.  In that case prioritise the *start* of the
        latest user prompt (falling back to the last bubble's start if no
        user bubble is visible), so the learner can read what they asked
        before the coach's reply flows off-screen downwards.
        """
        height = canvas.winfo_height()
        region = canvas.bbox('all')
        settled = height > 1 and region is not None
        if not settled:
            return 1.0, False             # nothing measured yet: aim low
        _, top, _, _, bottom = region
        total = max(1, bottom - top)
        if bottom - top <= height:
            return 0.0, True              # everything fits; keep it at top
        anchor = self._last_user_bubble() or None
        if anchor is None:
            children = self.thread.winfo_children()
            anchor = children[-1] if children else None
        if anchor is not None and anchor.winfo_exists() and anchor.winfo_ismapped():
            try:
                bbox = canvas.bbox(anchor.winfo_id())
            except Exception:
                bbox = None
            if bbox is not None:
                bubble_top, bubble_bottom = bbox[1], bbox[3]
                if bubble_bottom - bubble_top > height:
                    # Oversized newest bubble: align its top with the top of
                    # the viewport instead of showing only its tail.
                    return max(0.0, min((bubble_top - top) / total, 1.0)), True
        return 1.0, True

    def refresh(self):
        session = self.state.coach_session_by_id(self.session_id)
        q = session.questions[-1] if session and session.questions else None
        # Dismiss any visible tooltip before re-laying out the thread; stale
        # popups must never survive a state change or a moved pointer.  The
        # shared popup window is only hidden (never destroyed) here, which is
        # what keeps rapid refresh/hover churn from crashing Tk on Windows.
        Tooltip.dismiss_all()
        # Capture the stick state *before* tearing the bubbles down: once
        # the widgets are destroyed the canvas reports a degenerate yview
        # and the "was at the bottom" information would be lost.  If the
        # user was pinned to the bottom (or had never scrolled away), the
        # rebuilt thread follows its own end after relayout.
        if self._chat_canvas is not None and self._chat_canvas.winfo_exists():
            try:
                self._stick_to_bottom = self._stick_to_bottom or \
                    self._chat_canvas.yview()[1] >= 0.995
            except Exception:
                pass
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
                ChatBubble(self.thread, turn, session.sources, index,
                           self.navigate, role=turn.role).pack(fill='x', pady=4)
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
        # Follow the rebuilt thread once Tk has finished geometry (the
        # stick state was captured before the old bubbles were destroyed).
        self._schedule_thread_follow()

    def shutdown(self):
        self._hide_suggestions()
        if self._autoscroll_job is not None:
            try:
                self.after_cancel(self._autoscroll_job)
            except Exception:
                pass
            self._autoscroll_job = None
        for sid in {self.session_id, *(key[0] for key in self.service.assessments)}:
            self.service.cancel_turn(sid)
        self.events.stop()
