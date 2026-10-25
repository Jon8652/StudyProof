"""Different study topics keep separate identity and resumable loose ends."""
from dataclasses import asdict

from app.core.agent.coach_service import CoachService
from app.core.agent.questions import extract_questions, question_entries
from app.core.agent.schema import CoachReply
from app.core.models import CoachSession, CoachTurn, Dataset, Note, QuestionRecord, SourceRef
from app.core.recap import build_recap_payload
from app.core.result import ActionResult
from app.persistence.repository import dataset_to_payload
from app.persistence.validation import validate_payload
from app.state.app_state import AppState


def mixed_session():
    data = Dataset.empty()
    data.notes = [Note.create('Linear layer plus softmax', 'A linear layer transforms its inputs.'),
                  Note.create('Welcome — stranger', 'The stranger finds a letter in the attic.')]
    refs = [SourceRef('note', note.id, note.title) for note in data.notes]
    session = CoachSession.create(refs)
    q = QuestionRecord.create('What does the softmax function do?',
                               [{'source_id': refs[0].id, 'page': 1, 'text': data.notes[0].body}])
    session.questions.append(q)
    session.turns.append(CoachTurn.create('coach', 'question', q.prompt, q.id))
    data.coach_sessions.append(session)
    return data, session, q


def switched_legacy_session():
    data, session, q = mixed_session()
    switch = CoachTurn.create('user', 'answer', 'Can we study the stranger instead?', q.id)
    switch.answer_intent = False
    q.first_answer, q.first_answer_at, q.status = switch.text, switch.at_local, 'answered'
    session.turns.append(switch)
    follow = CoachTurn.create('coach', 'feedback',
                             'Of course. What does the stranger discover in the attic?', q.id)
    session.turns.append(follow)
    return data, session, q, follow


def test_mixed_sources_get_separate_titles_and_no_combined_source_label():
    data, session, q, follow = switched_legacy_session()
    before = asdict(data)
    payload = build_recap_payload(data)
    assert len(payload['loose_ends']) == 2
    by_question = {item['question']: item for item in payload['loose_ends']}
    assert by_question[q.prompt]['title'] == q.prompt
    assert by_question[q.prompt]['label'] == 'Linear layer plus softmax'
    novel = by_question['What does the stranger discover in the attic?']
    assert novel['title'] == novel['question']
    assert novel['turn_id'] == follow.id
    assert novel['label'] == ''  # no reliable per-question source attribution
    assert novel['question_id'] != q.id
    assert asdict(data) == before, 'building a recap must not mutate saved conversations'


def test_answer_to_legacy_inline_question_does_not_answer_skipped_original():
    data, session, q, follow = switched_legacy_session()
    session.turns.append(CoachTurn.create('user', 'revision', 'A letter in the attic.', q.id))
    payload = build_recap_payload(data)
    assert [item['question_id'] for item in payload['loose_ends']] == [q.id]
    # Explicitly resuming that older question must direct the next answer
    # back to it, even though another question was asked later in the log.
    service = CoachService(AppState(data))
    service.state._mark_dirty_and_save = lambda: None
    assert service.submit_message(session.id, 'Softmax converts scores to probabilities.', q.id).ok
    assert not build_recap_payload(data)['loose_ends']


def test_feedback_registers_new_question_and_next_answer_targets_it():
    data, session, q = mixed_session()
    state = AppState(data)
    state._mark_dirty_and_save = lambda: None
    state.settings.ai.api_key, state.settings.ai.base_url = 'fake-key', 'https://example.test/v1'
    service = CoachService(state)
    assert service.submit_message(session.id, 'Can we study the stranger instead?').ok
    job = service.prepare_turn(session.id, 'feedback', conversational=True).value
    assert service.prepare_assessment(job)
    assert service.complete_assessment(job, ActionResult(True, value=None)).ok
    reply = CoachReply('feedback', 'Of course. What does the stranger discover in the attic?')
    assert service.complete_turn(job, ActionResult(True, value=reply)).ok
    assert len(session.questions) == 2
    new = session.questions[-1]
    assert new.first_answer is None and new.status == 'open'
    assert session.turns[-1].question_id == new.id
    assert len(build_recap_payload(data)['loose_ends']) == 2
    assert service.submit_message(session.id, 'A letter in the attic.').ok
    assert new.first_answer == 'A letter in the attic.'
    assert q.first_answer == 'Can we study the stranger instead?'  # original turn is immutable
    assert [item['question_id'] for item in build_recap_payload(data)['loose_ends']] == [q.id]


def test_new_question_in_plain_feedback_is_saved_and_reopened():
    data, session, q = mixed_session()
    q.first_answer = 'It converts scores into probabilities.'
    q.status = 'answered'
    state = AppState(data)
    state._mark_dirty_and_save = lambda: None
    state.settings.ai.api_key, state.settings.ai.base_url = 'fake-key', 'https://example.test/v1'
    service = CoachService(state)
    job = service.prepare_turn(session.id, 'feedback', conversational=True).value
    assert service.complete_turn(job, ActionResult(True, value=CoachReply(
        'feedback', 'Good. What does the stranger discover in the attic?'))).ok
    loaded, _ = validate_payload(dataset_to_payload(data))
    links = build_recap_payload(loaded)['loose_ends']
    assert len(links) == 1 and links[0]['question_id'] == session.questions[-1].id
    assert links[0]['question'] == 'What does the stranger discover in the attic?'


def test_repeated_natural_questions_do_not_multiply_saved_source_spans():
    data, session, q = mixed_session()
    session.sources = session.sources[:1]
    state = AppState(data)
    state._mark_dirty_and_save = lambda: None
    state.settings.ai.api_key, state.settings.ai.base_url = 'fake-key', 'https://example.test/v1'
    service = CoachService(state)
    for i in range(6):
        assert service.submit_message(session.id, 'It transforms its inputs.').ok
        job = service.prepare_turn(session.id, 'feedback', conversational=True).value
        assert service.complete_turn(job, ActionResult(True, value=CoachReply(
            'feedback', f'Why does transformation {i} change the output?'))).ok
        assert len(session.questions[-1].spans) == len(job['pack'].spans)


def test_two_questions_in_one_reply_remain_distinct():
    data, session, q = mixed_session()
    session.turns.append(CoachTurn.create('coach', 'feedback',
        'What does the stranger find? Why does the stranger keep it?', q.id))
    links = build_recap_payload(data)['loose_ends']
    assert len(links) == 3
    assert len({item['question_id'] for item in links}) == 3


def test_legacy_full_reply_title_uses_question_not_unrelated_preface():
    data, session, q = mixed_session()
    text = ("Let's start a revision session on Linear layer plus softmax.\n\n"
            'Without looking, what single value is recorded on page 1 of your notes?')
    q.prompt = session.turns[0].text = text
    links = build_recap_payload(data)['loose_ends']
    assert len(links) == 1
    assert links[0]['title'].startswith('what single value')
    assert "Let's start" not in links[0]['title']


def test_acknowledgments_and_topic_requests_are_not_answers_but_uncertainty_is():
    for text in ('I see', 'Can we switch to biology?', 'Please explain softmax'):
        data, session, q = mixed_session()
        q.first_answer = text
        session.turns.append(CoachTurn.create('user', 'answer', text, q.id))
        assert len(build_recap_payload(data)['loose_ends']) == 1
    data, session, q = mixed_session()
    q.first_answer = "I'm not sure"
    session.turns.append(CoachTurn.create('user', 'answer', q.first_answer, q.id))
    assert not build_recap_payload(data)['loose_ends']


def test_social_invites_code_and_quoted_questions_are_not_study_loose_ends():
    assert not extract_questions('Which topic would you like to study next?')
    assert not extract_questions('Does that make sense?')
    assert not extract_questions('> What is in the attic?\n```\nWhy is this code here?\n```')
    assert extract_questions('Explain why softmax probabilities sum to one.')
    assert extract_questions('Can you explain how likelihood changes?')


def test_answer_intent_and_new_recap_link_fields_roundtrip():
    data, session, q, follow = switched_legacy_session()
    service = CoachService(AppState(data))
    service.state._mark_dirty_and_save = lambda: None
    job = service.prepare_recap(session.id).value
    assert service.complete_recap(job, service.generate_recap(job)).ok
    loaded, _ = validate_payload(dataset_to_payload(data))
    restored = loaded.coach_sessions[0]
    assert restored.turns[1].answer_intent is False
    assert restored.turns[-1].recap == session.turns[-1].recap
