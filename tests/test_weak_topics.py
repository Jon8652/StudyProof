"""Private assessment and the persistent per-topic revision lifecycle."""
import copy
import json

import pytest

from app.core.agent.coach_service import CoachService
from app.core.agent.provider_openai import OpenAICompatProvider
from app.core.agent.provider_base import ProviderError
from app.core.agent.schema import AnswerAssessment, CoachReply
from app.core.models import AISettings
from app.core.result import ActionResult
from app.persistence.repository import dataset_to_payload
from app.persistence.validation import validate_payload
from app.state.app_state import AppState


@pytest.fixture
def study(monkeypatch, scripted_provider):
    state = AppState()
    monkeypatch.setattr(state, '_mark_dirty_and_save', lambda: None)
    note = state.create_note('Biology', 'Chlorophyll absorbs light. Mitochondria release energy through respiration.').value
    state.settings.ai = AISettings('live', 'https://example.test/v1', api_key='test-key')
    service = CoachService(state, scripted_provider)
    session = service.start_session([('note', note.id)]).value
    assert service.request_question(session.id).ok
    return state, service, session, note


def answer(service, session, text='Plants eat sunlight.'):
    assert service.submit_message(session.id, text).ok
    job = service.prepare_turn(session.id, 'feedback', conversational=True).value
    assert service.prepare_assessment(job)
    return job


def commit(service, job, note, score, topic='Chlorophyll', question='What does chlorophyll do?', weak_id=None):
    return service.complete_assessment(job, ActionResult(True, value=AnswerAssessment(
        note.id, topic, question, score, weak_id)))


def visible(service, job):
    assert service.complete_turn(job, ActionResult(True, value=CoachReply(
        'feedback', 'Think about the role of light.'))).ok


def test_low_topic_persists_and_corrected_answer_clears_original_session(study):
    state, service, session, note = study
    job = answer(service, session)
    # Background assessment may finish before the visible reply.
    assert commit(service, job, note, 20).ok
    visible(service, job)
    weak = state.dataset.weak_topics[0]
    assert weak.source.id == note.id and weak.topic == 'Chlorophyll'
    assert weak.question == 'What does chlorophyll do?'
    assert session.questions[0].confidence == 'low'
    payload = dataset_to_payload(state.dataset)
    restored, warnings = validate_payload(payload)
    assert not warnings
    assert restored.weak_topics[0].id == weak.id
    assert restored.weak_topics[0].source == weak.source
    assert restored.weak_topics[0].question == weak.question
    assert restored.weak_topics[0].score == weak.score
    assert restored.weak_topics[0].updated_at == weak.updated_at.replace(microsecond=0)
    job = answer(service, session, 'Chlorophyll absorbs light for photosynthesis.')
    visible(service, job)
    assert commit(service, job, note, 95, weak_id=weak.id).ok
    assert not state.dataset.weak_topics
    assert session.questions[0].confidence == 'high'
    assert not any(t.kind == 'confidence' or t.confidence for t in session.turns)


def test_revision_session_survives_original_chat_deletion(study):
    state, service, session, note = study
    job = answer(service, session)
    visible(service, job)
    assert commit(service, job, note, 10).ok
    weak = state.dataset.weak_topics[0]
    assert service.delete_session(session.id).ok
    assert len(state.dataset.weak_topics) == 1
    revision = service.start_weak_topic_session(weak.id).value
    assert revision.questions[0].prompt == weak.question
    job = answer(service, revision, 'It absorbs light.')
    visible(service, job)
    assert commit(service, job, note, 80, weak_id=weak.id).ok
    assert len(state.dataset.weak_topics) == 1
    job = answer(service, revision, 'Chlorophyll absorbs the light that drives photosynthesis.')
    visible(service, job)
    assert commit(service, job, note, 90, weak_id=weak.id).ok
    assert not state.dataset.weak_topics


def test_distinct_topics_in_one_source_are_not_cleared_together(study):
    state, service, session, note = study
    job = answer(service, session)
    visible(service, job)
    assert commit(service, job, note, 30).ok
    job = answer(service, session, 'Mitochondria absorb light.')
    visible(service, job)
    assert commit(service, job, note, 20, 'Respiration', 'What do mitochondria do?').ok
    assert len(state.dataset.weak_topics) == 2
    first = state.dataset.weak_topics[0]
    job = answer(service, session, 'Chlorophyll absorbs light.')
    visible(service, job)
    assert commit(service, job, note, 95, weak_id=first.id).ok
    assert [w.topic for w in state.dataset.weak_topics] == ['Respiration']


@pytest.mark.parametrize('score,flagged', [(0, True), (69, True), (70, False), (84, False), (85, False), (100, False)])
def test_initial_thresholds(study, score, flagged):
    state, service, session, note = study
    job = answer(service, session)
    assert commit(service, job, note, score).ok
    assert bool(state.dataset.weak_topics) is flagged


def test_failed_assessment_never_blocks_coaching_or_clears_topics(study):
    state, service, session, note = study
    job = answer(service, session)
    visible(service, job)
    assert commit(service, job, note, 20).ok
    job = answer(service, session, 'I see')
    assert not service.complete_assessment(job, ActionResult(False, 'Bad format')).ok
    visible(service, job)
    assert len(state.dataset.weak_topics) == 1
    job = answer(service, session, 'Can you explain it?')
    assert service.complete_assessment(job, ActionResult(True, value=None)).ok
    visible(service, job)
    assert len(state.dataset.weak_topics) == 1


@pytest.mark.parametrize('change', ['cancel', 'source', 'dataset', 'deleted'])
def test_stale_assessments_cannot_mutate_weak_topics(study, change):
    state, service, session, note = study
    job = answer(service, session)
    visible(service, job)
    if change == 'cancel':
        service.cancel_turn(session.id)
    elif change == 'source':
        note.body = 'Edited'
    elif change == 'dataset':
        state.dataset = copy.deepcopy(state.dataset)
        state.rebuild_index()
    elif change == 'deleted':
        service.delete_session(session.id)
    assert not commit(service, job, note, 10).ok
    assert not state.dataset.weak_topics


def test_later_acknowledgment_does_not_discard_earlier_poor_answer(study):
    state, service, session, note = study
    poor = answer(service, session)
    visible(service, poor)
    acknowledgment = answer(service, session, 'I see')
    visible(service, acknowledgment)
    assert service.complete_assessment(acknowledgment, ActionResult(True, value=None)).ok
    assert commit(service, poor, note, 15).ok
    assert len(state.dataset.weak_topics) == 1


def test_older_result_cannot_reflag_topic_after_a_newer_correct_answer(study):
    state, service, session, note = study
    poor = answer(service, session)
    visible(service, poor)
    correct = answer(service, session, 'Chlorophyll absorbs light.')
    visible(service, correct)
    assert commit(service, correct, note, 95).ok
    assert not commit(service, poor, note, 15).ok
    assert not state.dataset.weak_topics


def test_newer_poor_answer_can_reflag_after_pending_mastery_clears(study):
    state, service, session, note = study
    first = answer(service, session)
    visible(service, first)
    assert commit(service, first, note, 20).ok
    weak = state.dataset.weak_topics[0]
    mastery = answer(service, session, 'Chlorophyll absorbs light.')
    visible(service, mastery)
    poor = answer(service, session, 'Actually it releases energy through respiration.')
    visible(service, poor)
    assert commit(service, mastery, note, 95, weak_id=weak.id).ok
    assert not state.dataset.weak_topics
    assert commit(service, poor, note, 20, weak_id=weak.id).ok
    assert len(state.dataset.weak_topics) == 1


def test_navigation_can_cancel_coaching_without_losing_background_assessment(study):
    state, service, session, note = study
    job = answer(service, session)
    service.cancel_turn(session.id, cancel_assessments=False)
    assert job['cancel'].is_set()
    assert not job['assessment_cancel'].is_set()
    assert commit(service, job, note, 20).ok
    assert len(state.dataset.weak_topics) == 1


def test_weak_register_validates_bad_records_and_older_save_files(study):
    state, _, _, _ = study
    payload = dataset_to_payload(state.dataset)
    payload.pop('weak_topics')
    restored, _ = validate_payload(payload)
    assert restored.weak_topics == []
    payload['weak_topics'] = [None, {}, {'id': 'bad', 'score': True}]
    restored, _ = validate_payload(payload)
    assert restored.weak_topics == []


class Response:
    def __init__(self, text):
        self.body = json.dumps({'choices': [{'message': {'content': text}}]}).encode()
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, limit): return self.body[:limit]


def provider_for(job, values):
    calls = []
    def transport(request, timeout):
        calls.append(json.loads(request.data))
        return Response(values[len(calls) - 1])
    return OpenAICompatProvider(job['settings'], transport), calls


def test_private_numeric_prompt_receives_actual_question_and_latest_answer(study):
    _, service, session, note = study
    job = answer(service, session, 'It captures light energy.')
    topic = json.dumps({'is_answer': True, 'source_id': note.id, 'topic': 'Chlorophyll',
                        'question': 'What does chlorophyll do?', 'weak_topic_id': None})
    provider, calls = provider_for(job, [topic, '92'])
    rating = provider.assess_answer(job)
    assert rating.score == 92
    payload = json.loads(calls[1]['messages'][1]['content'])
    assert payload['question'] == 'What does chlorophyll do?'
    assert payload['answer'] == 'It captures light energy.'
    assert 'SINGLE INTEGER' in calls[1]['messages'][0]['content']


@pytest.mark.parametrize('score', ['high', '{"score":90}', '90 out of 100', '-1', '101', '85.5', ''])
def test_invalid_numeric_ratings_are_rejected_without_coach_format_dependencies(study, score):
    _, service, session, note = study
    job = answer(service, session)
    topic = json.dumps({'is_answer': True, 'source_id': note.id, 'topic': 'Chlorophyll',
                        'question': 'What does chlorophyll do?'})
    provider, _ = provider_for(job, [topic, score])
    with pytest.raises(ProviderError):
        provider.assess_answer(job)


def test_acknowledgments_are_not_scored(study):
    _, service, session, _ = study
    job = answer(service, session, 'I see')
    provider, calls = provider_for(job, ['{"is_answer":false}'])
    assert provider.assess_answer(job) is None
    assert len(calls) == 1


def test_removed_source_keeps_record_but_prevents_revision(study):
    state, service, session, note = study
    job = answer(service, session)
    visible(service, job)
    assert commit(service, job, note, 10).ok
    weak = state.dataset.weak_topics[0]
    state.delete_note(note.id)
    assert not service.start_weak_topic_session(weak.id).ok
    assert state.dataset.weak_topics == [weak]
