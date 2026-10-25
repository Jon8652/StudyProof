"""Headless state machine. Generation is separate from main-thread commit."""
import copy
import threading
import uuid
from datetime import datetime

from app.core.result import ActionResult
from app.core.models import CoachTurn, QuestionRecord, WeakTopic
from app.core.agent.confidence import resolve_feedback_confidence
from app.core.agent.context import build_source_pack
from app.core.agent.grounding import PageIndex, validate_quotes
from app.core.agent.provider_openai import OpenAICompatProvider, sanitize_base_url
from app.core.agent.provider_base import ProviderError


class CoachService:
    def __init__(self, state, live_factory=OpenAICompatProvider):
        self.state = state
        self.live_factory = live_factory
        self.pending = {}
        self.assessments = {}
        self._assessment_versions = {}
        self._assessment_sequence = 0

    def prepare_recap(self, session_id, days=7, today=None):
        if days not in (7, 30):
            return ActionResult(False, 'Choose a 7-day or 30-day recap.')
        session = self.state.coach_session_by_id(session_id)
        if session_id and not session:
            return ActionResult(False, 'That session was deleted. Choose another session first.')
        self.cancel_turn(session_id, cancel_assessments=False)
        job = {'kind': 'recap', 'session_id': session_id, '_session': session,
               'revision': len(session.turns) if session else 0,
               'days': days, 'today': today or datetime.now().date(), 'cancel': threading.Event(),
               'settings': copy.deepcopy(self.state.settings.ai), '_dataset': self.state.dataset}
        self.pending[session_id] = job
        return ActionResult(True, value=job)

    def generate_recap(self, job):
        """Worker-only assembly and generation; offline is a successful result."""
        from app.core.recap import build_recap_payload, recap_sections, reconcile_recap, render_sections
        payload = build_recap_payload(job['_dataset'], job['days'], job['today'])
        job['payload'] = payload
        sections, simplified = recap_sections(payload), True
        if not job['cancel'].is_set() and job['settings'].api_key:
            try:
                reply = self.live_factory(job['settings']).generate(job)
                sections = reconcile_recap(reply, payload)
                simplified = sections != reply.sections
            except Exception:
                pass
        turn = CoachTurn.create('coach', 'recap', render_sections(sections))
        turn.recap = {'days': job['days'], 'loose_ends': payload['loose_ends'],
                      'offer': job['days'] == 7, 'revise': bool(payload['revision_topics']),
                      'simplified': simplified}
        return ActionResult(True, value=turn)

    def complete_recap(self, job, result):
        sid = job['session_id']
        if self.pending.get(sid) is not job or job['cancel'].is_set():
            return ActionResult(False, 'Recap cancelled.')
        self.pending.pop(sid, None)
        session = self.state.coach_session_by_id(sid)
        if (self.state.dataset is not job['_dataset'] or session is not job['_session']
                or (session and len(session.turns) != job['revision'])):
            return ActionResult(False, 'This session changed. Please request your recap again.')
        if result.ok and session:
            session.turns.append(result.value)
            self.state.persist_coach_session(session)
        return result

    def start_weak_topic_session(self, topic_id):
        topic = next((w for w in self.state.dataset.weak_topics if w.id == topic_id), None)
        if not topic:
            return ActionResult(False, 'That topic has already been cleared.')
        ref = self.state.source_ref_for(topic.source.kind, topic.source.id)
        if not ref:
            return ActionResult(False, 'This source was removed. Restore it before revising this topic.')
        result = self.start_session([ref])
        if result.ok:
            session = result.value
            question = QuestionRecord.create(topic.question)
            session.questions.append(question)
            session.turns.append(CoachTurn.create(
                'coach', 'question', f'Let’s revise {topic.topic}.\n\n{topic.question}',
                question.id, 'live'))
            self.state.persist_coach_session(session)
        return result

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

    def _store(self, session, question, kind, text, persist=True, **kwargs):
        mode = next((t.mode for t in reversed(session.turns) if t.mode), 'live')
        session.turns.append(CoachTurn.create(
            'user', kind, text, question_id=question.id, mode=mode, **kwargs))
        if persist:
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

    def submit_message(self, session_id, text, question_id=None):
        """Save a conversational message, including topic changes and retries."""
        s, q = self._find(session_id, question_id)
        if session_id in self.pending or not q or q.status == 'closed':
            return ActionResult(False, 'Start a question first, or wait for the coach.')
        if not text.strip():
            return ActionResult(False, 'Write a message first.')
        if q.first_answer is None:
            return self.submit_answer(session_id, text, question_id)
        q.revisions.append({'text': text.strip(), 'at': datetime.now().isoformat()})
        return self._store(s, q, 'revision', text.strip())

    def prepare_turn(self, session_id, kind, question_id=None, conversational=False):
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
        mode = 'live'
        try:
            if not settings.api_key:
                raise ProviderError('Add your AI key in Settings.')
            sanitize_base_url(settings.base_url)
        except ProviderError as exc:
            return ActionResult(False, str(exc))
        flag = threading.Event()
        job = {'session_id': s.id, 'kind': kind, 'pack': pack,
               'conversational': conversational,
               'question': copy.deepcopy(q), 'counter': len(s.questions),
               'history': [{'role': t.role, 'kind': t.kind, 'text': t.text}
                           for t in s.turns[-10:]], 'cancel': flag,
               'user_turn_id': next((t.id for t in reversed(s.turns) if t.role == 'user'), None),
               'weak_topics': [{'id': w.id, 'source_id': w.source.id,
                                'topic': w.topic, 'question': w.question}
                               for w in self.state.dataset.weak_topics
                               if any(r.id == w.source.id for r in s.sources)],
               'mode': mode, 'settings': settings,
               '_session': s,
               'revision': len(s.turns), 'question_id': q.id if q else None}
        self.pending[s.id] = job
        return ActionResult(True, value=job)

    def prepare_assessment(self, job):
        if not job.get('conversational') or job['kind'] != 'feedback' or not job['user_turn_id']:
            return False
        self._assessment_sequence += 1
        job['assessment_sequence'] = self._assessment_sequence
        job['assessment_cancel'] = threading.Event()
        self.assessments[(job['session_id'], job['user_turn_id'])] = job
        return True

    def generate_assessment(self, job):
        """Worker-safe and independent of visible reply generation."""
        try:
            provider = self.live_factory(job['settings'])
            return ActionResult(True, value=provider.assess_answer(job))
        except Exception:
            # Never turn an assessment/formatting failure into failed coaching
            # or treat an unassessed answer as mastery.
            return ActionResult(False, 'Background assessment unavailable. Weak topics were kept; send another answer to retry.')

    def complete_assessment(self, job, result):
        sid = job['session_id']
        key = (sid, job['user_turn_id'])
        if self.assessments.get(key) is not job:
            return ActionResult(False, 'Assessment superseded.')
        self.assessments.pop(key, None)
        session = self.state.coach_session_by_id(sid)
        if job['assessment_cancel'].is_set() or session is not job['_session']:
            return ActionResult(False, 'Assessment cancelled.')
        latest = next((t for t in session.turns if t.id == job['user_turn_id']), None)
        if not latest:
            return ActionResult(False, 'Assessment superseded.')
        notes = {n.id: n for n in self.state.dataset.notes}
        docs = {d.id: d for d in self.state.dataset.documents}
        if build_source_pack([(r.kind, r.id) for r in session.sources], notes, docs) != job['pack']:
            return ActionResult(False, 'Sources changed; weak topics were left unchanged.')
        if not result.ok or result.value is None:
            return result
        rating = result.value
        source = next((r for r in session.sources if r.id == rating.source_id), None)
        if (not source or type(rating.score) is not int or not 0 <= rating.score <= 100
                or not isinstance(rating.topic, str) or not rating.topic.strip()
                or not isinstance(rating.question, str) or not rating.question.strip()):
            return ActionResult(False, 'Invalid assessment; weak topics were left unchanged.')
        topics = self.state.dataset.weak_topics
        match = next((w for w in topics if w.source.id == source.id and
                      (w.id == rating.weak_topic_id or
                       w.topic.casefold().strip() == rating.topic.casefold().strip())), None)
        if rating.weak_topic_id and not match:
            if not any(w['id'] == rating.weak_topic_id and w['source_id'] == source.id
                       and w['topic'] == rating.topic for w in job['weak_topics']):
                return ActionResult(False, 'That weak topic changed; assessment skipped.')
        topic_key = (source.id, (match.topic if match else rating.topic).strip().casefold())
        version = (latest.at_local, job['assessment_sequence'])
        if self._assessment_versions.get(topic_key, (datetime.min, 0)) > version:
            return ActionResult(False, 'Assessment superseded.')
        self._assessment_versions[topic_key] = version
        q = session.question_by_id(latest.question_id)
        if q and q.confidence is None and q.first_answer == latest.text:
            q.prompt = rating.question
        elif not q or q.prompt.strip().casefold() != rating.question.strip().casefold():
            q = next((item for item in session.questions
                      if item.prompt.strip().casefold() == rating.question.strip().casefold()), None)
            if not q:
                q = QuestionRecord.create(rating.question, job['pack'].as_spans_payload())
                q.first_answer, q.first_answer_at = latest.text, latest.at_local
                q.status, q.feedback_given = 'answered', True
                session.questions.append(q)
                session.questions.sort(key=lambda item: item.first_answer_at or datetime.max)
        q.confidence = 'high' if rating.score >= 85 else 'medium' if rating.score >= 70 else 'low'
        q.confidence_at = datetime.now()
        latest.question_id = q.id
        # Keep the original scoring evidence on its answer turn. Current weak
        # topics are mutable and cannot alone establish historical improvement.
        latest.assessment = {'source_id': source.id, 'topic': rating.topic.strip(),
                             'question': rating.question.strip(), 'score': rating.score}
        if match:
            latest.assessment.update(previous_score=match.score,
                                     previous_at=match.updated_at.isoformat())
        if rating.score >= 85:
            if match:
                topics.remove(match)
        elif match:
            match.score, match.updated_at = rating.score, datetime.now()
        elif rating.score < 70:
            topics.append(WeakTopic(str(uuid.uuid4()), source, rating.topic.strip(),
                                    rating.question.strip(), session.id, q.id, rating.score))
        self.state.persist_coach_session(session)
        return ActionResult(True)

    def generate_turn(self, job):
        """Worker-safe: no state mutation, persistence or GUI calls."""
        try:
            provider = self.live_factory(job['settings'])
            return ActionResult(True, value=provider.generate(job))
        except ProviderError as exc:
            key = job['settings'].api_key
            message = str(exc).replace(key, '<redacted>') if key else str(exc)
            return ActionResult(False, message)
        except Exception:
            return ActionResult(False, 'The coach could not finish this turn. Try again.')

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
        if kind == 'feedback' and not job.get('conversational'):
            try:
                confidence = resolve_feedback_confidence(q, reply)
            except ProviderError as exc:
                return ActionResult(False, str(exc))
        if kind in ('question', 'followup'):
            parent = q if kind == 'followup' else None
            if q:
                q.status = 'closed'
            q = QuestionRecord.create(reply.text, job['pack'].as_spans_payload(),
                                      parent.id if parent else None)
            if parent:
                parent.followup_ids.append(q.id)
            s.questions.append(q)
            # Question citations can reveal the answer before the unaided attempt.
            quotes = []
        elif kind == 'hint':
            q.hints_seen += 1
        elif kind == 'feedback':
            # Confidence is no longer self-reported; the coach rates how well
            # the question was answered when it delivers the feedback.
            if not job.get('conversational'):
                q.confidence = confidence
                q.confidence_at = datetime.now()
            q.feedback_given = True
        if kind == 'feedback' and not job.get('conversational'):
            self._store(s, q, 'confidence', f'Coach confidence rating: {q.confidence}',
                        confidence=q.confidence, persist=False)
        mode = 'live'
        s.turns.append(CoachTurn.create('coach', kind, reply.text, q.id, mode,
                                        quotes=quotes, ungrounded_count=failures,
                                        confidence=q.confidence if kind == 'feedback' and not job.get('conversational') else None))
        self.state.persist_coach_session(s)
        return ActionResult(True, value=q)

    def cancel_turn(self, session_id, cancel_assessments=True):
        job = self.pending.pop(session_id, None)
        if job:
            job['cancel'].set()
        for key, assessment in list(self.assessments.items()):
            if key[0] == session_id and cancel_assessments:
                self.assessments.pop(key, None)
                assessment['assessment_cancel'].set()
        return ActionResult(True)

    def _request(self, session_id, kind, question_id=None):
        result = self.prepare_turn(session_id, kind, question_id)
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
