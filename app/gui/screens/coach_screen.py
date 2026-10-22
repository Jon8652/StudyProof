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
from app.gui.widgets.chat_bubble import ChatBubble
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
        self.header = ctk.CTkLabel(self.content, text='', wraplength=650, justify='left')
        self.header.pack(fill='x')
        # Pane 2: the chat thread.  Unstyled CTkScrollableFrame used to show
        # customtkinter's default grey; now the same blue pane surface.
        self.thread = ctk.CTkScrollableFrame(self.content, fg_color=PANE_BG)
        self.thread.pack(fill='both', expand=True)
        # Responsive wrapping for the coach text: the header/notice labels
        # pack fill='x', so their <Configure> width tracks the window.  The
        # wraplength follows it (clamped), making the text column slightly
        # narrower in a small windowed layout and wider when maximised —
        # matching how ChatBubble wraps its messages.
        self._wrap_job = None
        self._responsive_labels = []
        self.content.bind('<Configure>', self._schedule_wrap_update, add='+')
        # Private background ratings never appear in the conversation.
        self.confidence_note = ctk.CTkLabel(self.content, text='', text_color='#888888')
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
        self.notice = ctk.CTkLabel(self.content, text='', wraplength=650)
        self.notice.pack(fill='x')
        self._responsive_labels = [self.header, self.notice]
        self.refresh()

    # ------------------------------------------------------------------
    # Responsive coach-text wrapping

    def _schedule_wrap_update(self, event=None):
        # Debounce resize storms: only the last Configure in a burst does
        # the (cheap, guarded) relayout work.
        if event is not None and event.widget is not self.content:
            return
        if self._wrap_job is not None:
            try:
                self.after_cancel(self._wrap_job)
            except Exception:
                pass
        self._wrap_job = self.after(50, self._apply_responsive_wrapping)

    def _apply_responsive_wrapping(self):
        self._wrap_job = None
        try:
            width = self.content.winfo_width()
        except Exception:
            return
        if width <= 1 or not self.winfo_exists():
            return                        # not mapped yet; wait for Configure
        scaling = 1.0
        try:
            scaling = float(self._get_widget_scaling()) or 1.0
        except Exception:
            pass
        # Tk reports physical pixels; CTk wraplength uses unscaled ones.
        logical = max(1, int(width / scaling))
        from app.gui.widgets.chat_bubble import MAX_WRAPLENGTH, MIN_WRAPLENGTH
        target = max(MIN_WRAPLENGTH, min(MAX_WRAPLENGTH, logical - 8))
        for label in self._responsive_labels:
            try:
                if label.winfo_exists() and label.cget('wraplength') != target:
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

    def shutdown(self):
        self._hide_suggestions()
        for sid in {self.session_id, *(key[0] for key in self.service.assessments)}:
            self.service.cancel_turn(sid)
        self.events.stop()
