"""Recap facts, safety, provider boundaries and persistence without live AI."""
import json
import threading
import time
from datetime import date, datetime, timedelta

import pytest

from app.core.models import CoachSession, CoachTurn, Dataset, QuestionRecord, Session, SourceRef, WeakTopic
from app.core.recap import build_recap_payload, recap_sections, recap_section_options, render_sections
from app.core.agent.coach_service import CoachService
from app.core.agent.parser import parse_reply
from app.core.agent.prompt import messages
from app.core.agent.provider_base import ProviderError
from app.core.agent.provider_openai import OpenAICompatProvider
from app.core.agent.schema import CoachReply
from app.persistence.repository import dataset_to_payload
from app.persistence.validation import validate_payload
from app.state.app_state import AppState

TODAY = date(2026, 10, 25)  # UK clocks change today; windows use calendar dates.
REF = SourceRef('note', 'biology', 'Biology')


def logged(day, minutes):
    return Session.create(minutes, now=datetime.combine(day, datetime.min.time()) + timedelta(hours=12))


def routine(minutes=100):
    data = Dataset.empty()
    start = TODAY - timedelta(days=6)
    data.sessions = [logged(start - timedelta(days=offset), minutes) for offset in range(1, 29)]
    return data


def question_session(day=TODAY, prompt='How does photosynthesis work?'):
    moment = datetime.combine(day, datetime.min.time()) + timedelta(hours=10)
    session = CoachSession.create([REF], now=moment)
    q = QuestionRecord.create(prompt)
    session.questions.append(q)
    session.turns.append(CoachTurn.create('coach', 'question', prompt, q.id, at=moment))
    return session, q


def rated(session, question, day, score, topic='Photosynthesis'):
    turn = CoachTurn.create('user', 'answer', 'An answer', question.id,
                            at=datetime.combine(day, datetime.min.time()) + timedelta(hours=11))
    turn.assessment = {'source_id': REF.id, 'topic': topic, 'question': question.prompt, 'score': score}
    session.turns.append(turn)
    return turn


def service_for(data):
    state = AppState(data)
    state._mark_dirty_and_save = lambda: None
    return state, CoachService(state)


def test_windows_start_day_attribution_and_baseline_excludes_window():
    data = routine()
    start = TODAY - timedelta(days=6)
    crossing = Session.create(120, now=datetime.combine(start, datetime.min.time()) + timedelta(minutes=30))
    data.sessions += [crossing, logged(start, 40), logged(TODAY, 50), logged(TODAY + timedelta(days=1), 300)]
    p = build_recap_payload(data, today=TODAY)
    assert p['minutes'] == 90 and p['sessions'] == 2 and p['active_days'] == 2
    assert p['baseline_minutes'] == pytest.approx((2800 + 120) / 4)
    assert build_recap_payload(data, 30, TODAY)['minutes'] > p['minutes']
    assert build_recap_payload(data, 30, TODAY)['start'] == '2026-09-26'


@pytest.mark.parametrize('minutes,pattern', [(69, 'low'), (70, 'steady'), (100, 'steady'), (160, 'heavy')])
def test_tone_tolerance_and_high_volume_alone(minutes, pattern):
    data = routine()
    data.sessions.append(logged(TODAY, minutes * 7))
    p = build_recap_payload(data, today=TODAY)
    assert p['pattern'] == pattern
    text = render_sections(recap_sections(p)).lower()
    assert 'break' not in text and 'lighter day' not in text


def test_heavy_run_and_declining_answers_corroborate_rest():
    data = routine()
    data.sessions += [logged(TODAY - timedelta(days=i), 250) for i in range(7)]
    assert build_recap_payload(data, today=TODAY)['pattern'] == 'rest'
    data.sessions = data.sessions[:28] + [logged(TODAY, 1400)]
    session, q = question_session(TODAY - timedelta(days=5))
    for offset, score in enumerate([95, 95, 40, 30]):
        rated(session, q, TODAY - timedelta(days=4 - offset), score)
    data.coach_sessions.append(session)
    assert build_recap_payload(data, today=TODAY)['pattern'] == 'rest'


def test_thin_history_no_comparisons_and_explicit_goal_precedence():
    data = Dataset.empty()
    data.sessions = [logged(TODAY - timedelta(days=7), 300), logged(TODAY, 10)]
    p = build_recap_payload(data, today=TODAY)
    assert p['pattern'] == 'new' and p['baseline_minutes'] is None
    assert 'routine' not in render_sections(recap_sections(p))
    assert len(recap_sections(p)['next_step']) == 1
    data = routine()
    data.sessions.append(logged(TODAY, 100))
    data.settings.weekly_study_goal_minutes = 100
    p = build_recap_payload(data, today=TODAY)
    assert p['pattern'] == 'steady' and p['expected_minutes'] == 100


def test_weak_topic_history_improvement_and_persistent_weakness():
    data = Dataset.empty()
    session, q = question_session(TODAY - timedelta(days=20))
    rated(session, q, TODAY - timedelta(days=10), 30)
    rated(session, q, TODAY, 90)
    q.status, q.first_answer = 'answered', 'An answer'
    other = QuestionRecord.create('What is osmosis?')
    session.questions.append(other)
    rated(session, other, TODAY - timedelta(days=10), 40, 'Osmosis')
    rated(session, other, TODAY, 30, 'Osmosis')
    data.coach_sessions.append(session)
    data.weak_topics.append(WeakTopic('w', REF, 'Osmosis', other.prompt, session.id, other.id, 30, datetime(2026, 10, 25)))
    p = build_recap_payload(data, today=TODAY)
    assert p['improved'] == ['Photosynthesis']
    assert p['still_weak'] == ['Osmosis']
    assert p['topics'][0] == 'Photosynthesis'
    assert p['coached']
    # A mutable legacy entry removed without recorded assessments proves no improvement.
    data.coach_sessions.clear()
    assert not build_recap_payload(data, today=TODAY)['improved']


@pytest.mark.parametrize('previous_score,new_score', [(30, 75), (75, 90)])
def test_first_new_assessment_preserves_legacy_weak_evidence(previous_score, new_score):
    data = Dataset.empty()
    session, q = question_session()
    turn = rated(session, q, TODAY, new_score)
    turn.assessment.update(previous_score=previous_score, previous_at='2026-10-10T10:00:00')
    data.coach_sessions.append(session)
    if new_score < 85:
        data.weak_topics.append(WeakTopic('w', REF, 'Photosynthesis', q.prompt, session.id,
                                          q.id, new_score, datetime(2026, 10, 25)))
    p = build_recap_payload(data, today=TODAY)
    assert p['improved'] == ['Photosynthesis'] and not p['still_weak']
    assert p['revision_topics'] == (['Photosynthesis'] if new_score < 85 else [])
    loaded, _ = validate_payload(dataset_to_payload(data))
    assert loaded.coach_sessions[0].turns[-1].assessment == turn.assessment


def test_loose_ends_active_sessions_recent_first_and_cap():
    data = Dataset.empty()
    for i in range(8):
        session, q = question_session(TODAY - timedelta(days=i))
        data.coach_sessions.append(session)
    # An older session becomes eligible through real practice in the window.
    old, q = question_session(TODAY - timedelta(days=40))
    old.turns.append(CoachTurn.create('coach', 'hint', 'A hint', q.id, at=datetime(2026, 10, 25)))
    data.coach_sessions.append(old)
    p = build_recap_payload(data, today=TODAY)
    assert len(p['loose_ends']) == 5 and p['more_loose_ends'] == 3
    assert p['loose_ends'][0]['session_id'] == data.coach_sessions[0].id
    assert 'older ones' in recap_sections(p)['loose_ends']


def test_recap_is_not_practice_and_long_lists_fit_limit():
    data = Dataset.empty()
    session, q = question_session()
    session.turns.clear()
    session.turns.append(CoachTurn.create('coach', 'recap', 'Summary', at=datetime(2026, 10, 25)))
    data.coach_sessions.append(session)
    assert not build_recap_payload(data, today=TODAY)['coached']
    for i in range(30):
        rated(session, q, TODAY, 40, str(i) + 'A' * 159)
    p = build_recap_payload(data, today=TODAY)
    assert len(p['topics']) == 6 and p['more_topics'] == 24
    assert len(render_sections(recap_sections(p))) <= 1500


def test_offline_persists_reopens_and_cancel_stores_nothing():
    data = Dataset.empty()
    session, q = question_session()
    data.coach_sessions.append(session)
    state, service = service_for(data)
    job = service.prepare_recap(session.id, today=TODAY).value
    result = service.generate_recap(job)
    assert service.complete_recap(job, result).ok
    turn = session.turns[-1]
    assert turn.kind == 'recap' and turn.recap['offer'] and turn.recap['simplified']
    assert turn.recap['loose_ends'][0]['question_id'] == q.id
    loaded, _ = validate_payload(dataset_to_payload(data))
    assert loaded.coach_sessions[0].turns[-1].recap == turn.recap
    job = service.prepare_recap(session.id, 30, TODAY).value
    result = service.generate_recap(job)
    service.cancel_turn(session.id)
    assert not service.complete_recap(job, result).ok and len(session.turns) == 2
    job = service.prepare_recap(session.id, 30, TODAY).value
    assert service.complete_recap(job, service.generate_recap(job)).ok
    assert not session.turns[-1].recap['offer']


def test_empty_data_preview_creates_no_session_and_deleted_target_rejects_commit():
    state, service = service_for(Dataset.empty())
    job = service.prepare_recap(None, today=TODAY).value
    result = service.complete_recap(job, service.generate_recap(job))
    assert result.ok and not state.dataset.coach_sessions
    assert 'No sessions recorded' in result.value.text
    assert '0 minutes on 0 days' in result.value.text
    note = state.create_note('Biology', 'Study text').value
    session = state.create_coach_session([state.source_ref_for('note', note.id)]).value
    job = service.prepare_recap(session.id, today=TODAY).value
    result = service.generate_recap(job)
    state.delete_coach_session(session.id)
    assert not service.complete_recap(job, result).ok


@pytest.mark.parametrize('failure', [False, True])
def test_provider_wrong_figures_and_failure_use_local_facts(failure):
    data = Dataset.empty()
    data.sessions.append(logged(TODAY, 30))
    state, service = service_for(data)
    state.settings.ai.api_key = 'fake-key'
    class Provider:
        def __init__(self, settings):
            pass
        def generate(self, context):
            if failure:
                raise ProviderError('Offline')
            return CoachReply('recap', '', meta={'pattern': 'new'}, sections={
                'effort': '9999 minutes on invented topic Hacking.', 'learning': 'Invented improvement',
                'loose_ends': 'Resume evil-session', 'next_step': ['You must catch up.']})
    service.live_factory = Provider
    job = service.prepare_recap(None, today=TODAY).value
    turn = service.generate_recap(job).value
    assert '30 minutes' in turn.text and '9999' not in turn.text and 'evil-session' not in turn.text
    assert turn.recap['simplified'] and not turn.recap['loose_ends']


def test_prompt_allowlist_validated_alternatives_and_assessment_roundtrip():
    data = Dataset.empty()
    session, q = question_session()
    turn = rated(session, q, TODAY, 30)
    data.coach_sessions.append(session)
    p = build_recap_payload(data, today=TODAY)
    prompt = messages({'kind': 'recap', 'payload': p, 'secret': 'never-send'})
    assert 'never-send' not in str(prompt) and q.id not in str(prompt) and session.id not in str(prompt)
    sections = recap_sections(p)
    sections['effort'] = recap_section_options(p)['effort'][1]
    raw = json.dumps({'kind': 'recap', 'sections': sections, 'meta': {'pattern': p['pattern']}})
    reply = parse_reply(raw, 'recap')
    assert reply.sections == sections
    loaded, _ = validate_payload(dataset_to_payload(data))
    assert loaded.coach_sessions[0].turns[-1].assessment == turn.assessment
    sections['next_step'] = ['a', 'b', 'c']
    with pytest.raises(ValueError):
        parse_reply(json.dumps({'kind': 'recap', 'sections': sections, 'meta': {'pattern': 'new'}}), 'recap')


def test_recap_provider_single_repair_and_timeout_budget():
    from app.core.models import AISettings
    p = build_recap_payload(Dataset.empty(), today=TODAY)
    valid = json.dumps({'kind': 'recap', 'sections': recap_sections(p), 'meta': {'pattern': 'new'}})
    calls = []
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self, size):
            content = 'malformed' if len(calls) == 1 else valid
            return json.dumps({'choices': [{'message': {'content': content}}]}).encode()
    def transport(request, timeout):
        calls.append((json.loads(request.data), timeout))
        return Response()
    provider = OpenAICompatProvider(AISettings(base_url='https://example.test/v1', api_key='fake-key'), transport)
    reply = provider.generate({'kind': 'recap', 'payload': p, 'cancel': threading.Event()})
    assert reply.kind == 'recap' and len(calls) == 2
    assert calls[1][1] <= calls[0][1]


def test_payload_performance_10k_study_500_coach_sessions():
    data = routine()
    data.sessions *= 360
    for _ in range(500):
        session, q = question_session()
        rated(session, q, TODAY, 50)
        data.coach_sessions.append(session)
    started = time.perf_counter()
    build_recap_payload(data, today=TODAY)
    assert time.perf_counter() - started < .2
