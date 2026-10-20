"""Headless state machine. Generation is separate from main-thread commit."""
import copy
import threading
from datetime import datetime

from app.core.result import ActionResult
from app.core.models import CoachTurn, QuestionRecord
from app.core.agent.confidence import resolve_feedback_confidence
from app.core.agent.context import build_source_pack
from app.core.agent.grounding import PageIndex, validate_quotes
from app.core.agent.mock_engine import MockProvider
from app.core.agent.provider_openai import OpenAICompatProvider, sanitize_base_url
from app.core.agent.provider_base import ProviderError


class CoachService:
    def __init__(self, state, live_factory=OpenAICompatProvider):
        self.state = state
        self.live_factory = live_factory
        self.pending = {}

    def start_session(self, selection):
        refs = [self.state.source_ref_for(*s) if isinstance(s, tuple) else s for s in selection]
        if any(s is None for s in refs):
            return ActionResult(False, 'One of those sources was removed. Select sources again.')
        return self.state.create_coach_session(refs)

    def delete_session(self, session_id):
        self.cancel_turn(session_id)
        return self.state.delete_coach_session(session_id)

    def _find(self, session_id, question_id=None):
        s = self.state.coach_session_by_id(session_id)
        q = (s.question_by_id(question_id) if question_id else s.questions[-1] if s.questions else None) if s else None
        return s, q

    def _store(self, session, question, kind, text, **kwargs):
        mode = next((t.mode for t in reversed(session.turns) if t.mode), 'mock')
        session.turns.append(CoachTurn.create(
            'user', kind, text, question_id=question.id, mode=mode, **kwargs))
        self.state.persist_coach_session(session)
        return ActionResult(True, value=question)

    def submit_answer(self, session_id, text, question_id=None):
        s, q = self._find(session_id, question_id)
        if session_id in self.pending or not q or q.status != 'open' or q.first_answer is not None:
            return ActionResult(False, 'The first answer is saved once. Use a revision after feedback.')
        if not text.strip():
            return ActionResult(False, 'Write your answer first.')
        q.first_answer, q.first_answer_at, q.status = text.strip(), datetime.now(), 'answered'
        return self._store(s, q, 'answer', q.first_answer)

    def submit_revision(self, session_id, text, question_id=None):
        s, q = self._find(session_id, question_id)
        if session_id in self.pending or not q or q.status == 'closed' or not q.feedback_given:
            return ActionResult(False, 'Get feedback before adding a revision.')
        if not text.strip():
            return ActionResult(False, 'Write your revision first.')
        q.revisions.append({'text': text.strip(), 'at': datetime.now().isoformat()})
        return self._store(s, q, 'revision', text.strip())

    def prepare_turn(self, session_id, kind, question_id=None, force_mock=False):
        s, q = self._find(session_id, question_id)
        if not s:
            return ActionResult(False, 'Choose a coaching session first.')
        if session_id in self.pending:
            return ActionResult(False, 'Wait for this turn or cancel it first.')
        if kind == 'question':
            if q and q.status != 'closed' and not q.feedback_given:
                return ActionResult(False, 'Finish your answer and get feedback first.')
        elif kind in ('hint', 'feedback', 'followup'):
            if not q or q.first_answer is None or q.status == 'closed':
                return ActionResult(False, 'Submit your answer first to unlock hints and feedback.')
            if kind == 'followup' and not q.feedback_given:
                return ActionResult(False, 'Get feedback before asking a follow-up.')
        else:
            return ActionResult(False, 'Choose a valid coaching action.')
        notes = {n.id: n for n in self.state.dataset.notes}
        docs = {d.id: d for d in self.state.dataset.documents}
        pack = build_source_pack([(r.kind, r.id) for r in s.sources], notes, docs)
        if not pack.spans:
            return ActionResult(False, 'Select at least one note or document containing study text first.')
        settings = copy.deepcopy(self.state.settings.ai)
        mode = 'mock' if force_mock else settings.mode
        if mode == 'live':
            try:
                if not settings.api_key:
                    raise ProviderError('Add your AI key in Settings, or continue this turn in Mock.')
                sanitize_base_url(settings.base_url)
            except ProviderError as exc:
                return ActionResult(False, str(exc))
        flag = threading.Event()
        job = {'session_id': s.id, 'kind': kind, 'pack': pack,
               'question': copy.deepcopy(q), 'counter': len(s.questions),
               'history': [{'role': t.role, 'kind': t.kind, 'text': t.text}
                           for t in s.turns[-10:]], 'cancel': flag,
               'mode': mode, 'settings': settings,
               '_session': s,
               'revision': len(s.turns), 'question_id': q.id if q else None}
        self.pending[s.id] = job
        return ActionResult(True, value=job)

    def generate_turn(self, job):
        """Worker-safe: no state mutation, persistence or GUI calls."""
        try:
            provider = self.live_factory(job['settings']) if job['mode'] == 'live' else MockProvider()
            return ActionResult(True, value=provider.generate(job))
        except ProviderError as exc:
            key = job['settings'].api_key
            message = str(exc).replace(key, '<redacted>') if key else str(exc)
            return ActionResult(False, message)
        except Exception:
            return ActionResult(False, 'The coach could not finish this turn. Try again or continue in Mock.')

    def complete_turn(self, job, result):
        sid = job['session_id']
        if self.pending.get(sid) is not job or job['cancel'].is_set():
            return ActionResult(False, 'Turn cancelled.')
        self.pending.pop(sid, None)
        if not result.ok:
            return result
        s, q = self._find(sid, job['question_id'])
        if not s or s is not job['_session'] or len(s.turns) != job['revision']:
            return ActionResult(False, 'This session changed. Please try the turn again.')
        reply = result.value
        if reply.kind != job['kind']:
            return ActionResult(False, 'The coach returned a different action. Please try again.')
        notes = {n.id: n for n in self.state.dataset.notes}
        docs = {d.id: d for d in self.state.dataset.documents}
        current_pack = build_source_pack([(r.kind, r.id) for r in s.sources], notes, docs)
        if current_pack != job['pack']:
            return ActionResult(False, 'Your sources changed during this turn. Please try again.')
        quotes, failures = validate_quotes(reply.quotes, PageIndex(s.sources, notes, docs), job['pack'])
        kind = job['kind']
        if kind in ('question', 'followup'):
            parent = q if kind == 'followup' else None
            if q:
                q.status = 'closed'
            q = QuestionRecord.create(reply.text, job['pack'].as_spans_payload(),
                                      parent.id if parent else None,
                                      reply.meta.get('answer_key', {})
                                      if job['mode'] == 'mock' or reply.meta.get('fallback_notice') else {})
            if parent:
                parent.followup_ids.append(q.id)
            s.questions.append(q)
            # Question citations can leak a cloze answer before the unaided attempt.
            quotes = []
        elif kind == 'hint':
            q.hints_seen += 1
        elif kind == 'feedback':
            # Confidence is no longer self-reported; the coach rates how well
            # the question was answered when it delivers the feedback.
            q.confidence = resolve_feedback_confidence(q, reply)
            q.confidence_at = datetime.now()
            q.feedback_given = True
        mode = 'mock' if reply.meta.get('fallback_notice') else job['mode']
        s.turns.append(CoachTurn.create('coach', kind, reply.text, q.id, mode,
                                        quotes=quotes, ungrounded_count=failures,
                                        confidence=q.confidence if kind == 'feedback' else None))
        notice = reply.meta.get('fallback_notice')
        if notice:
            s.turns.append(CoachTurn.create('coach', 'notice', notice, q.id, mode))
        self.state.persist_coach_session(s)
        return ActionResult(True, notice, q)

    def cancel_turn(self, session_id):
        job = self.pending.pop(session_id, None)
        if job:
            job['cancel'].set()
        return ActionResult(True)

    def _request(self, session_id, kind, question_id=None, force_mock=False):
        result = self.prepare_turn(session_id, kind, question_id, force_mock)
        return self.complete_turn(result.value, self.generate_turn(result.value)) if result.ok else result

    def rate_confidence(self, session_id, confidence, question_id=None):
        """Deprecated: confidence is now coach-assigned with the feedback turn.

        Kept so older callers/scripts fail gracefully instead of crashing.
        """
        return ActionResult(False, 'Confidence is assigned by the coach after feedback.')

    def request_question(self, session_id, **kwargs):
        return self._request(session_id, 'question', **kwargs)

    def request_hint(self, session_id, **kwargs):
        return self._request(session_id, 'hint', **kwargs)

    def request_feedback(self, session_id, **kwargs):
        return self._request(session_id, 'feedback', **kwargs)

    def request_followup(self, session_id, **kwargs):
        return self._request(session_id, 'followup', **kwargs)
