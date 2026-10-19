"""Headless contracts for phases 4–6."""
import json
import threading
from dataclasses import asdict
from urllib.error import HTTPError, URLError

import pytest

from app.core.models import Dataset, Note, SourceRef, AISettings
from app.core.agent.context import build_source_pack
from app.core.agent.coach_service import CoachService
from app.core.agent.grounding import PageIndex, validate_quotes
from app.core.agent.mock_engine import MockProvider
from app.core.agent.parser import parse_reply
from app.core.agent.prompt import messages
from app.core.agent.provider_openai import OpenAICompatProvider, sanitize_base_url
from app.core.agent.provider_base import ProviderError
from app.core.agent.schema import Quote
from app.core.result import ActionResult
from app.state.app_state import AppState

TEXT = ('Photosynthesis converts sunlight into chemical energy stored in glucose, '
        'while chlorophyll absorbs the light needed for this process. '
        'Mitochondria release usable energy through respiration, supplying the cell '
        'with the energy needed for growth and maintenance.')


@pytest.fixture
def coach(monkeypatch):
    note = Note.create('Biology', TEXT)
    state = AppState(Dataset(notes=[note]))
    monkeypatch.setattr(state, '_mark_dirty_and_save', lambda: None)
    service = CoachService(state)
    session = service.start_session([('note', note.id)]).value
    return state, service, session


def unlock(service, session):
    assert service.request_question(session.id).ok
    assert service.submit_answer(session.id, 'Sunlight and energy').ok
    assert service.rate_confidence(session.id, 'low').ok


def test_state_machine(coach):
    state, service, session = coach
    assert not service.request_feedback(session.id).ok
    assert service.request_question(session.id).ok
    q = session.questions[0]
    before = len(session.turns)
    for action in (service.request_question, service.request_hint, service.request_feedback,
                   service.request_followup):
        assert not action(session.id).ok
    assert not service.rate_confidence(session.id, 'high').ok
    assert not service.submit_revision(session.id, 'revision').ok
    assert not service.submit_answer(session.id, ' ').ok
    assert len(session.turns) == before
    assert service.submit_answer(session.id, 'Original').ok
    timestamp = q.first_answer_at
    assert not service.submit_answer(session.id, 'changed').ok
    assert not service.rate_confidence(session.id, 'invalid').ok
    assert not service.request_hint(session.id).ok
    assert service.rate_confidence(session.id, 'low').ok
    assert not service.rate_confidence(session.id, 'high').ok
    assert not service.request_followup(session.id).ok
    assert service.request_hint(session.id).ok
    assert q.hints_seen == 1
    assert service.request_feedback(session.id).ok
    assert not service.submit_revision(session.id, '').ok
    assert service.submit_revision(session.id, 'Better explanation').ok
    assert service.submit_revision(session.id, 'Another revision').ok
    assert q.first_answer == 'Original' and q.first_answer_at == timestamp
    assert len(q.revisions) == 2
    assert service.request_followup(session.id).ok
    child = session.questions[-1]
    assert child.parent_id == q.id and child.id in q.followup_ids
    assert q.status == 'closed'
    assert not service.request_hint(session.id).ok
    assert not service.submit_revision(session.id, 'closed', q.id).ok
    assert service.submit_answer(session.id, 'Child answer').ok
    assert state.get_review_metrics().activity.questions_answered == 2


def test_determinism_grounding_and_memory(coach):
    state, service, session = coach
    job = service.prepare_turn(session.id, 'question').value
    a = MockProvider().generate(job)
    assert a == MockProvider().generate(job)
    assert '____' in a.text and not a.quotes
    assert service.complete_turn(job, ActionResult(True, value=a)).ok
    assert service.submit_answer(session.id, 'Unrelated').ok
    assert service.rate_confidence(session.id, 'medium').ok
    for _ in range(12):
        assert service.request_hint(session.id).ok
    hint = session.turns[-1]
    assert hint.quotes[0]['verified']
    job = service.prepare_turn(session.id, 'feedback').value
    assert len(job['history']) == 10
    payload = json.loads(messages(job)[1]['content'])
    assert len(payload['history']) == 10
    assert 'api_key' not in json.dumps(payload)
    service.cancel_turn(session.id)


def test_grounding(coach):
    state, _, session = coach
    note = state.dataset.notes[0]
    note.body = 'Photosynthesis   Converts\nsunlight into energy.'
    index = PageIndex(session.sources, {note.id: note}, {})
    quotes = [Quote(note.id, 1, 'photosynthesis converts sunlight'),
              Quote(note.id, 2, 'sunlight'), Quote('absent', 1, 'sunlight'),
              Quote(note.id, 1, 'invented'), Quote(note.id, 1, '')]
    valid, count = validate_quotes(quotes, index)
    assert count == 4 and valid[0]['quote'] == 'Photosynthesis Converts sunlight'
    pack = build_source_pack([('note', note.id)], {note.id: note}, {})
    pack.spans[0].text = 'Different text'
    assert validate_quotes(quotes[:1], index, pack) == ([], 1)


@pytest.mark.parametrize('wrapper', ['{}', '```json\n{}\n```', 'some prose {} trailing'])
def test_parser(wrapper):
    raw = json.dumps({'kind': 'hint', 'text': 'Brace } inside "text"', 'quotes': [], 'meta': {}})
    assert parse_reply(wrapper.format(raw), 'hint').text.startswith('Brace')


@pytest.mark.parametrize('raw', ['truncated {', '[]', '{"kind":"x","text":"a"}',
    '{"kind":"hint","text":4}', '{"kind":"hint","text":""}'])
def test_parser_rejects(raw):
    with pytest.raises(ValueError):
        parse_reply(raw)


class Response:
    def __init__(self, content):
        self.body = json.dumps({'choices': [{'message': {'content': content}}]}).encode()
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, limit): return self.body[:limit]


def settings():
    return AISettings('live', 'https://example.test/v1/', 'test-model', 'super-secret-key')


def test_provider_payload_and_repair(coach):
    _, service, session = coach
    job = service.prepare_turn(session.id, 'question').value
    calls = []
    def transport(request, timeout):
        calls.append((request, timeout))
        return Response('malformed' if len(calls) == 1 else json.dumps({
            'kind': 'question', 'text': 'Explain energy.', 'quotes': [], 'meta': {}}))
    provider = OpenAICompatProvider(settings(), transport)
    assert provider.generate(job).kind == 'question'
    assert len(calls) == 2
    request, timeout = calls[0]
    assert request.full_url == 'https://example.test/v1/chat/completions'
    assert request.get_header('Authorization') == 'Bearer super-secret-key'
    assert 0 < timeout <= 30
    assert b'super-secret-key' not in request.data
    assert json.loads(request.data)['max_tokens'] == 700


@pytest.mark.parametrize('code', [401, 403, 400, 429, 500, 503])
def test_http_errors_redaction_and_retries(coach, code):
    _, service, session = coach
    job = service.prepare_turn(session.id, 'question').value
    calls = []
    class NoWait:
        def is_set(self): return False
        def wait(self, seconds): assert seconds == 2; return False
    job['cancel'] = NoWait()
    def transport(request, timeout):
        calls.append(timeout)
        raise HTTPError(request.full_url, code, 'super-secret-key', {}, None)
    with pytest.raises(ProviderError) as exc:
        OpenAICompatProvider(settings(), transport).generate(job)
    assert 'super-secret-key' not in str(exc.value)
    assert len(calls) == (2 if code == 429 or code >= 500 else 1)
    assert not session.questions and not session.turns


@pytest.mark.parametrize('error', [TimeoutError('super-secret-key'), URLError('super-secret-key'), RuntimeError('super-secret-key')])
def test_transport_failures(coach, error):
    _, service, session = coach
    job = service.prepare_turn(session.id, 'question').value
    def transport(*args, **kwargs): raise error
    with pytest.raises(ProviderError) as exc:
        OpenAICompatProvider(settings(), transport).generate(job)
    assert 'super-secret-key' not in str(exc.value)


def test_mock_fallback_and_cancel(coach):
    state, service, session = coach
    job = service.prepare_turn(session.id, 'question').value
    reply = OpenAICompatProvider(settings(), lambda *a, **k: Response('broken')).generate(job)
    assert reply.meta['fallback_notice']
    service.cancel_turn(session.id)
    assert job['cancel'].is_set()
    assert not service.complete_turn(job, ActionResult(True, value=reply)).ok
    assert not session.questions
    assert service.request_question(session.id).ok
    assert service.delete_session(session.id).ok
    assert not state.dataset.coach_sessions


@pytest.mark.parametrize('url', ['file:///tmp/x', 'https://user:secret@host', 'https://x?q=secret', '', 'https://x:bad'])
def test_bad_urls(url):
    with pytest.raises(ProviderError): sanitize_base_url(url)


def test_missing_key_and_sources(coach):
    state, service, session = coach
    state.settings.ai = settings()
    state.settings.ai.api_key = ''
    assert not service.request_question(session.id).ok
    assert service.request_question(session.id, force_mock=True).ok
    state.dataset.notes.clear()
    assert session.sources and session.questions
    assert not service.request_hint(session.id).ok


def test_sources_changed_or_dataset_replaced_during_turn(coach):
    import copy
    state, service, session = coach
    job = service.prepare_turn(session.id, 'question').value
    result = service.generate_turn(job)
    state.dataset.notes[0].body = 'Edited study text.'
    assert not service.complete_turn(job, result).ok
    assert not session.turns and not session.questions
    job = service.prepare_turn(session.id, 'question').value
    result = service.generate_turn(job)
    state.dataset = copy.deepcopy(state.dataset)
    state.rebuild_index()
    assert not service.complete_turn(job, result).ok
    assert not state.dataset.coach_sessions[0].turns


def test_failed_turn_does_not_mutate_and_can_retry(coach):
    state, service, session = coach
    unlock(service, session)
    state.settings.ai = settings()
    class FailedProvider:
        def __init__(self, settings): pass
        def generate(self, context): raise ProviderError('Rejected super-secret-key')
    service.live_factory = FailedProvider
    before = len(session.turns)
    result = service.request_feedback(session.id)
    assert not result.ok and 'super-secret-key' not in result.message
    assert len(session.turns) == before
    assert not session.questions[0].feedback_given
    assert service.request_feedback(session.id, force_mock=True).ok


def test_source_deletion_preserves_history(coach):
    state, service, session = coach
    unlock(service, session)
    assert service.request_feedback(session.id).ok
    quotes = session.turns[-1].quotes
    assert state.delete_note(state.dataset.notes[0].id).ok
    assert session in state.dataset.coach_sessions
    assert validate_quotes(quotes, PageIndex(session.sources, {}, {}))[1] == 1
    assert not service.request_followup(session.id).ok


def test_missing_key_never_calls_transport(coach):
    _, service, session = coach
    job = service.prepare_turn(session.id, 'question').value
    config = settings()
    config.api_key = ''
    def forbidden(*args, **kwargs): pytest.fail('No request is allowed without a key')
    with pytest.raises(ProviderError):
        OpenAICompatProvider(config, forbidden).generate(job)


def test_retry_success_test_connection_and_redaction(coach):
    _, service, session = coach
    job = service.prepare_turn(session.id, 'question').value
    class NoWait:
        def is_set(self): return False
        def wait(self, seconds): return False
    job['cancel'] = NoWait()
    calls = []
    def transport(request, timeout):
        calls.append(timeout)
        if len(calls) == 1:
            raise HTTPError(request.full_url, 429, 'busy', {}, None)
        return Response(json.dumps({'kind': 'question', 'text': 'super-secret-key', 'quotes': [], 'meta': {}}))
    provider = OpenAICompatProvider(settings(), transport)
    assert provider.generate(job).text == '<redacted>'
    assert len(calls) == 2
    assert provider.test_connection() == 'Connected to the AI service.'
    assert calls[-1] <= 10
    assert 'super-secret-key' not in repr(settings())


def test_core_has_no_gui_or_persistence_imports():
    import ast
    from pathlib import Path
    for path in Path('app/core/agent').glob('*.py'):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            names = ([node.module or ''] if isinstance(node, ast.ImportFrom)
                     else [a.name for a in node.names] if isinstance(node, ast.Import) else [])
            assert not any(n.startswith(('app.gui', 'app.persistence', 'app.state', 'tkinter', 'customtkinter')) for n in names)


@pytest.mark.parametrize('change', [
    {'text': 'a' * 2001}, {'quotes': [{}] * 6}, {'meta': []},
    {'quotes': [{'source_id': 'x', 'page': True, 'quote': 'a'}]},
    {'quotes': [{'source_id': 'x', 'page': 0, 'quote': 'a'}]},
    {'quotes': [{'source_id': 'x', 'page': 1, 'quote': ''}]}])
def test_schema_caps(change):
    value = {'kind': 'hint', 'text': 'Think about energy.', 'quotes': [], 'meta': {}}
    value.update(change)
    with pytest.raises(ValueError):
        parse_reply(json.dumps(value))


def test_old_cancelled_response_cannot_replace_new_job(coach):
    _, service, session = coach
    old = service.prepare_turn(session.id, 'question').value
    response = service.generate_turn(old)
    service.cancel_turn(session.id)
    new = service.prepare_turn(session.id, 'question').value
    assert not service.complete_turn(old, response).ok
    assert service.pending[session.id] is new
    assert service.complete_turn(new, service.generate_turn(new)).ok
    assert len(session.turns) == 1


def test_every_coach_mutation_broadcasts(coach):
    state, service, session = coach
    notifications = []
    state.add_listener(lambda: notifications.append(True))
    unlock(service, session)
    assert service.request_hint(session.id).ok
    assert service.request_feedback(session.id).ok
    assert service.submit_revision(session.id, 'A revision').ok
    assert len(notifications) == 6
    before = len(notifications)
    assert not service.submit_answer(session.id, 'Try overwrite').ok
    assert len(notifications) == before
