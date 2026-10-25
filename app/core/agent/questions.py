"""Question identity and pending work, including questions in natural replies.

Only literal questions already present in the transcript are recovered. Titles
and links never depend on a new model call or on unrelated selected sources.
"""
import re
import uuid


def normalise_question(text):
    return ' '.join(re.sub(r'[*_`#]', '', text).casefold().split()).strip(' .!?')


def short_title(text, limit=85):
    text = ' '.join(re.sub(r'[*_`#]', '', text).split())
    text = re.sub(r'^(?:without looking|in your own words|quick question|think about this)[,:—\s]+',
                  '', text, flags=re.I)
    if len(text) <= limit:
        return text or 'Study question'
    return text[:limit - 1].rsplit(' ', 1)[0].rstrip(' ,:;') + '…'


_SOCIAL_QUESTION = re.compile(
    r'(?:what|which)\s+(?:topic|subject|material|notes?)\b.*(?:next|study|revise|explore)'
    r'|(?:would|do)\s+you\s+(?:like|want)\b|are\s+you\s+ready\b|can\s+i\s+help\b'
    r'|^(?:does that make sense|is that clear|shall we|any questions|how can i help)', re.I)


def extract_questions(text, explicit=False):
    # Quoted notes/code are not new questions posed by the Coach.
    clean = re.sub(r'```[\s\S]*?```', '', text)
    clean = '\n'.join(line for line in clean.splitlines() if not line.lstrip().startswith('>'))
    parts = re.split(r'(?<=[.!?])\s+|\n+', clean)
    found = []
    for part in parts:
        part = re.sub(r'^\s*(?:[-*•]|\d+[.)]|#{1,6})\s*', '', part).strip()
        if not part or _SOCIAL_QUESTION.search(part):
            continue
        if '?' in part:
            # Ignore anything after the final question mark (e.g. a closing
            # invitation) and keep the exact question wording before it.
            found.append(part[:part.rfind('?') + 1])
        elif re.match(r'^(?:explain|describe|compare|calculate|define|name|identify|outline)\b', part, re.I):
            found.append(part)
    if not found and explicit and clean.strip():
        found = [clean.strip()]
    return list(dict.fromkeys(found))


def is_discussion_message(text):
    """Conservative fallback for old turns with no saved answer intent."""
    value = ' '.join(text.casefold().strip().split()).rstrip('.!?')
    if value in ('hi', 'hello', 'thanks', 'thank you', 'i see', 'okay', 'ok', 'got it'):
        return True
    return bool(re.match(
        r"^(?:(?:can|could|shall) (?:we|you)|(?:i(?:'d| would) like|i want|let(?:'s| us))|please) "
        r"(?:please )?(?:study|revise|switch|change|move|explore|explain|help|go (?:back|over|through))\b",
        value))


def question_entries(session):
    """Return one entry per question with local identity and answer evidence.

    Legacy free-text feedback may contain questions without QuestionRecords.
    Stable IDs and turn IDs let those entries be resumed and materialised on
    demand, without mutating the save just by requesting a recap.
    """
    records = {q.id: q for q in session.questions}
    by_prompt = {normalise_question(q.prompt): q for q in session.questions}
    refs = {ref.id: ref for ref in session.sources}
    ratings = {}
    user_turns = {}
    for turn in session.turns:
        if turn.role == 'user':
            user_turns.setdefault(turn.question_id, []).append(turn)
        if turn.assessment:
            ratings[normalise_question(turn.assessment['question'])] = turn.assessment
    entries, by_id = [], {}
    latest = None

    def add(prompt, turn, record=None):
        key = normalise_question(prompt)
        identity = record.id if record else str(uuid.uuid5(
            uuid.NAMESPACE_URL, f'coach-question:{session.id}:{turn.id}:{key}'))
        if identity in by_id:
            return by_id[identity]
        rating = ratings.get(key)
        source_ids = ({rating['source_id']} if rating else
                      {span.get('source_id') for span in record.spans} if record else set())
        source_ids &= refs.keys()
        if not source_ids and len(refs) == 1:
            source_ids = set(refs)
        # Ambiguous selection is not evidence that this question is about
        # every note/PDF. Show no source rather than joining unrelated titles.
        source = refs[next(iter(source_ids))] if len(source_ids) == 1 else None
        item = {'session_id': session.id, 'question_id': identity,
                'turn_id': turn.id if turn else None, 'prompt': prompt,
                'title': short_title(rating['topic'] if rating else prompt),
                'label': source.label if source else '',
                'source_ids': sorted(source_ids),
                'at': turn.at_local if turn else session.created_at_local,
                'answered': False, '_record': record}
        if record and record.first_answer is not None:
            attempts = user_turns.get(record.id, [])
            # Existing transcript turns are assigned in chronological order
            # below. Old replies reused an earlier question's ID, so counting
            # every turn with that ID here could answer the wrong topic.
            item['answered'] = not attempts or (turn is None and any(
                t.answer_intent is True or t.assessment or
                (t.answer_intent is None and not is_discussion_message(t.text)) for t in attempts))
        entries.append(item)
        by_id[identity] = item
        return item

    for turn in session.turns:
        if turn.role == 'coach' and turn.kind in ('question', 'followup', 'feedback'):
            prompts = extract_questions(turn.text, explicit=turn.kind in ('question', 'followup'))
            for prompt in prompts:
                key = normalise_question(prompt)
                record = records.get(turn.question_id)
                if record and key != normalise_question(record.prompt) and key not in normalise_question(record.prompt):
                    record = None
                if record and record.id in by_id and key != normalise_question(by_id[record.id]['prompt']):
                    record = None
                record = record or by_prompt.get(key)
                latest = add(prompt, turn, record)
        elif turn.role == 'user':
            if turn.answer_intent is False or (turn.answer_intent is None and not turn.assessment
                                               and is_discussion_message(turn.text)):
                continue
            target = by_id.get(turn.question_id)
            # Old feedback replies kept the previous question's ID even
            # after asking a new question. Follow the literal latest question
            # in that case; assessed/explicitly resumed answers keep their ID.
            if latest and latest['_record'] is None and not turn.assessment and turn.answer_intent is not True:
                target = latest
            if target:
                target['answered'] = True
    for record in session.questions:
        if record.id not in by_id:
            add(record.prompt, None, record)
    for item in entries:
        item.pop('_record')
    return entries


def register_reply_questions(session, turn, source_spans=()):
    """Register literal new questions before the next message is composed."""
    from app.core.models import QuestionRecord
    questions = []
    for item in question_entries(session):
        if item['turn_id'] != turn.id or session.question_by_id(item['question_id']):
            continue
        # Use this request's bounded source material once. Combining every
        # earlier question's copied spans would multiply them on each reply.
        spans = [span for span in source_spans if span.get('source_id') in item['source_ids']]
        question = QuestionRecord(id=item['question_id'], prompt=item['prompt'], spans=spans)
        session.questions.append(question)
        questions.append(question)
    if questions:
        # One reply may contain multiple questions; each keeps its own record
        # and recap link. The last one becomes the default composer target.
        turn.question_id = questions[-1].id
    return questions
