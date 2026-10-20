"""Thin coaching UI; workers only generate, main thread commits."""
import threading
import customtkinter as ctk
from app.core.agent.coach_service import CoachService
from app.core.agent.grounding import PageIndex
from app.gui.screens.base_screen import BaseScreen
from app.gui.dialogs.source_select import SourceSelectDialog
from app.gui.dialogs.sessions_dialog import SessionsDialog
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
        self.events = UIEventQueue(self)
        self.events.start()
        top = ctk.CTkFrame(self.content)
        top.pack(fill='x')
        ctk.CTkButton(top, text='New session', command=self.new_session).pack(side='left', padx=4, pady=4)
        ctk.CTkButton(top, text='Sessions…', command=self.sessions).pack(side='left', padx=4)
        self.header = ctk.CTkLabel(self.content, text='', wraplength=700, justify='left')
        self.header.pack(fill='x')
        self.thread = ctk.CTkScrollableFrame(self.content)
        self.thread.pack(fill='both', expand=True)
        # No manual confidence buttons: the coach rates confidence itself,
        # based on how the question was answered (see feedback turns).
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
        composer_row = ctk.CTkFrame(self.content, corner_radius=18)
        composer_row.pack(fill='x', pady=(4, 0))
        self.composer = ctk.CTkTextbox(composer_row, height=85, border_width=0)
        self.composer.pack(side='left', fill='x', expand=True, padx=6, pady=6)
        self.composer.bind('<Return>', self.send)
        self.composer.bind('<Shift-Return>', lambda event: None)
        self.composer.bind('<KeyRelease>', self._typing, add='+')
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
        self.notice = ctk.CTkLabel(self.content, text='', wraplength=700)
        self.notice.pack(fill='x')
        self.fallback = ctk.CTkButton(self.content, text='Continue this turn in Mock', command=self.mock_fallback)
        self.refresh()

    def new_session(self):
        if self.busy:
            self.cancel()
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
            self.cancel()
        dialog = SessionsDialog(self, self.state, self.service)
        self.wait_window(dialog)
        if dialog.result:
            self.resume_session(dialog.result)
        self.refresh()

    def resume_session(self, sid):
        self.cancel()
        self.session_id = sid
        self.refresh()

    def request(self, kind, force_mock=False):
        self._last_action = kind
        self.fallback.pack_forget()
        prepared = self.service.prepare_turn(self.session_id, kind, force_mock=force_mock)
        if not prepared.ok:
            self.notice.configure(text=prepared.message)
            if self.state.settings.ai.mode == 'live':
                self.fallback.pack(pady=3)
            return
        job = prepared.value
        self.busy = True
        self.notice.configure(text='Coach is thinking…')
        self.refresh()
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

    def finished(self, job, result):
        if self.service.pending.get(job['session_id']) is not job:
            return
        self.busy = False
        result = self.service.complete_turn(job, result)
        self.notice.configure(text=result.message or '')
        if not result.ok and job['mode'] == 'live':
            self.fallback.pack(pady=3)
        self.refresh()

    def mock_fallback(self):
        if self._last_action:
            self.request(self._last_action, True)

    def cancel(self):
        self.service.cancel_turn(self.session_id)
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
        """Send an unaided answer or revision, then request coach feedback."""
        if self.busy:
            return 'break'
        session = self.state.coach_session_by_id(self.session_id)
        q = session.questions[-1] if session and session.questions else None
        text = self.composer.get('1.0', 'end').strip()
        if not q or q.status == 'closed':
            return 'break'
        if q.first_answer is not None and not q.feedback_given:
            self.request('feedback')
            return 'break'
        if not text:
            return 'break'
        if q.first_answer is None:
            result = self.service.submit_answer(self.session_id, text)
        elif q.feedback_given:
            result = self.service.submit_revision(self.session_id, text)
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
            mode = next((t.mode for t in reversed(session.turns) if t.mode), 'mock')
            self.header.configure(text=f'{mode.title()} · ' + ' · '.join(labels))
            for turn in session.turns:
                if turn.kind == 'confidence':
                    continue
                ChatBubble(self.thread, turn, session.sources, index, self.navigate).pack(fill='x', pady=4)
        else:
            self.header.configure(text='Pick sources to start a coached session.')
        # Confidence is assigned by the coach when feedback is delivered —
        # there are no manual rating buttons any more.
        if q and q.confidence:
            note = f'Coach confidence rating: {q.confidence}'
        elif q and q.first_answer is not None:
            note = 'Confidence is rated by the coach once you get feedback.'
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
        can_send = bool(q and q.status != 'closed' and not self.busy)
        self.send_button.configure(state='normal' if can_send else 'disabled')
        self.composer.configure(state='disabled' if self.busy else 'normal')

    def shutdown(self):
        self._hide_suggestions()
        self.service.cancel_turn(self.session_id)
        self.events.stop()
