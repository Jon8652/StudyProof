"""Thin coaching UI; workers only generate, main thread commits."""
import threading
import customtkinter as ctk
from app.core.agent.coach_service import CoachService
from app.core.agent.grounding import PageIndex
from app.gui.screens.base_screen import BaseScreen
from app.gui.dialogs.source_select import SourceSelectDialog
from app.gui.dialogs.sessions_dialog import SessionsDialog
from app.gui.widgets.chat_bubble import ChatBubble
from app.gui.widgets.citation_chip import Tooltip
from app.gui.workers import UIEventQueue


class CoachScreen(BaseScreen):
    def __init__(self, master, state):
        super().__init__(master, state, title='Study Coach')
        self.service = CoachService(state)
        self.session_id = None
        self.busy = False
        self._last_action = None
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
        self.composer = ctk.CTkTextbox(self.content, height=85)
        self.composer.pack(fill='x')
        row = ctk.CTkFrame(self.content, fg_color='transparent')
        row.pack(fill='x', pady=5)
        self.buttons = {}
        for key, label, command in [
            ('question', 'Question', lambda: self.request('question')),
            ('answer', 'Submit answer', self.answer),
            ('hint', 'Hint', lambda: self.request('hint')),
            ('feedback', 'Feedback', lambda: self.request('feedback')),
            ('revision', 'Revision', self.revise),
            ('followup', 'Follow-up', lambda: self.request('followup')),
            ('cancel', 'Cancel', self.cancel)]:
            button = ctk.CTkButton(row, text=label, width=85, command=command)
            button.pack(side='left', padx=2)
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
        # Dismiss any lingering gate tooltip before re-laying out the thread;
        # stale popups must never survive a state change or a moved pointer.
        for tooltip in list(Tooltip._popups):
            try:
                tooltip.destroy()
            except Exception:
                pass
        Tooltip._popups.clear()
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
        gates = {
            'question': (bool(session) and (not q or q.feedback_given), 'Finish this question and get feedback first.'),
            'answer': (bool(q and q.first_answer is None), 'The first answer is saved once; revise after feedback.'),
            'hint': (bool(q and q.first_answer is not None and q.status != 'closed'), 'Submit your answer first to unlock hints.'),
            'feedback': (bool(q and q.first_answer is not None and q.status != 'closed'), 'Submit your answer first to get feedback.'),
            'revision': (bool(q and q.feedback_given and q.status != 'closed'), 'Get feedback before adding a revision.'),
            'followup': (bool(q and q.feedback_given and q.status != 'closed'), 'Get feedback before a follow-up.'),
            'cancel': (self.busy, 'There is no turn in progress.')}
        for key, (enabled, explanation) in gates.items():
            enabled = enabled and (not self.busy or key == 'cancel')
            tip_text = '' if enabled else ('Wait or cancel the current turn.' if self.busy else explanation)
            button = self.buttons[key]
            button.configure(state='normal' if enabled else 'disabled')
            # Hover text only: set_text hides any popup that might be showing,
            # so explanations never stay visible once the mouse moves away.
            button.gate_tip.set_text(tip_text)
        self.composer.configure(state='disabled' if self.busy else 'normal')

    def shutdown(self):
        self.service.cancel_turn(self.session_id)
        self.events.stop()
