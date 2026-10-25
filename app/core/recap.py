"""Local recap facts and tone rules. No provider, persistence or GUI access."""
from collections import Counter
from datetime import date, datetime, timedelta
from app.core.agent.questions import question_entries

LOW_RATIO = .70
HEAVY_RATIO = 1.50
MIN_HISTORY_DAYS = 14
MIN_PRIOR_SESSIONS = 3
BASELINE_DAYS = 28
HEAVY_DAY_RUN = 3
TOPIC_CAP = 6
LOOSE_END_CAP = 5


def build_recap_payload(dataset, days=7, today=None):
    if days not in (7, 30):
        raise ValueError('Choose a 7-day or 30-day recap.')
    today = today or date.today()
    start = today - timedelta(days=days - 1)
    prior_start = start - timedelta(days=BASELINE_DAYS)
    totals, prior = Counter(), Counter()
    count = prior_count = 0
    for session in tuple(dataset.sessions):
        if start <= session.day <= today:
            totals[session.day] += session.duration_minutes
            count += 1
        elif prior_start <= session.day < start:
            prior[session.day] += session.duration_minutes
            prior_count += 1
    reliable = bool(prior and prior_count >= MIN_PRIOR_SESSIONS
                    and (start - min(prior)).days >= MIN_HISTORY_DAYS)
    baseline_span = min(BASELINE_DAYS, (start - min(prior)).days) if prior else BASELINE_DAYS
    baseline_minutes = sum(prior.values()) / baseline_span * days if reliable else None
    goal = getattr(dataset.settings, 'weekly_study_goal_minutes', 0)
    expected = goal * days / 7 if goal > 0 else baseline_minutes
    basis = 'your chosen goal' if goal > 0 else 'your recent routine'
    topics, labels = Counter(), {}
    events, loose = [], []
    coached = False
    confidence_events = []
    for session in tuple(dataset.coach_sessions):
        turns = tuple(session.turns)
        questions = tuple(session.questions)
        question_map = {q.id: q for q in questions}
        sources = {ref.id: ref.label for ref in session.sources}
        question_topics = {}
        for turn in turns:
            rating = turn.assessment
            if rating and turn.at_local.date() <= today:
                key = (rating['source_id'], rating['topic'].strip().casefold())
                labels[key] = rating['topic'][:160]
                if 'previous_score' in rating and 'previous_at' in rating:
                    previous_at = datetime.fromisoformat(rating['previous_at'])
                    if previous_at.date() <= today:
                        events.append((previous_at, key, rating['previous_score'], True))
                events.append((turn.at_local, key, rating['score'], False))
                question_topics[turn.question_id] = key
                if start <= turn.at_local.date() <= today:
                    confidence_events.append((turn.at_local, rating['score']))
        active = start <= session.created_at_local.date() <= today
        for turn in turns:
            day = turn.at_local.date()
            # Recaps and navigation must not manufacture practice activity.
            if turn.kind not in ('question', 'followup', 'answer', 'revision', 'hint', 'feedback'):
                continue
            if not start <= day <= today:
                continue
            active = coached = True
            key = ((turn.assessment['source_id'], turn.assessment['topic'].strip().casefold())
                   if turn.assessment else question_topics.get(turn.question_id))
            if key:
                topics[key] += 1
            else:
                question = question_map.get(turn.question_id)
                source_ids = {s.get('source_id') for s in question.spans} if question else set()
                for source_id, label in sources.items():
                    if source_ids and source_id not in source_ids:
                        continue
                    key = (source_id, label.casefold())
                    labels[key] = label[:160]
                    topics[key] += 1
        if not active or session.created_at_local.date() > today:
            continue
        for entry in question_entries(session):
            moment = entry['at']
            if entry['answered'] or moment.date() > today:
                continue
            loose.append({'session_id': session.id, 'question_id': entry['question_id'],
                          'turn_id': entry['turn_id'] or '', 'title': entry['title'],
                          'question': entry['prompt'][:110], 'label': entry['label'][:90],
                          'asked': moment.strftime('%a %d %b'), '_at': moment})

    # Replay the actual current Coach rule: <70 flags, >=85 clears, 70–84
    # retains the existing flag. Never resurrect the retired REV-1 heuristic.
    before, current = {}, {}
    for moment, key, score, was_registered in sorted(events, key=lambda event: event[0]):
        old = current.get(key)
        flagged = was_registered or score < 70 or (score < 85 and old is not None and old['weak'])
        item = {'weak': flagged, 'score': score, 'at': moment}
        current[key] = item
        if moment.date() < start:
            before[key] = item
    weak = {}
    for topic in tuple(dataset.weak_topics):
        if topic.updated_at.date() > today:
            continue
        key = (topic.source.id, topic.topic.strip().casefold())
        labels[key] = topic.topic[:160]
        weak[key] = topic
        # Legacy saves retain only the last score. It is evidence of prior
        # weakness only when its timestamp predates this window.
        if key not in before and topic.updated_at.date() < start:
            before[key] = {'weak': True, 'score': topic.score, 'at': topic.updated_at}
    improved = []
    still = []
    for key, old in before.items():
        if not old['weak']:
            continue
        now = current.get(key)
        if now and start <= now['at'].date() <= today and (not now['weak'] or now['score'] > old['score']):
            improved.append(labels[key])
        elif key in weak:
            still.append(key)
    ordered_weak = sorted(weak, key=lambda key: (weak[key].score, -weak[key].updated_at.timestamp()))
    weak_rank = {key: rank for rank, key in enumerate(ordered_weak)}
    still.sort(key=weak_rank.get)
    loose.sort(key=lambda item: item['_at'], reverse=True)
    for item in loose:
        item.pop('_at')
    heavy_run = run = 0
    if reliable:
        typical_day = sum(prior.values()) / len(prior)
        for offset in range(days):
            day = start + timedelta(days=offset)
            run = run + 1 if totals[day] > typical_day * HEAVY_RATIO else 0
            heavy_run = max(heavy_run, run)
    confidence_events.sort()
    scores = [score for _, score in confidence_events]
    falling = len(scores) >= 4 and sum(scores[len(scores)//2:]) / len(scores[len(scores)//2:]) < sum(scores[:len(scores)//2]) / len(scores[:len(scores)//2])
    minutes = sum(totals.values())
    pattern = 'new'
    if expected:
        ratio = minutes / expected
        pattern = 'low' if ratio < LOW_RATIO else 'steady'
        if ratio > HEAVY_RATIO:
            pattern = 'rest' if heavy_run >= HEAVY_DAY_RUN or falling else 'heavy'
    return {'days': days, 'start': start.isoformat(), 'end': today.isoformat(),
            'minutes': minutes, 'sessions': count, 'active_days': sum(v > 0 for v in totals.values()),
            'baseline_minutes': baseline_minutes, 'expected_minutes': expected, 'basis': basis,
            'pattern': pattern, 'coached': coached,
            'has_sources': bool(dataset.notes or dataset.documents),
            'topics': [labels[key] for key, _ in topics.most_common(TOPIC_CAP)],
            'more_topics': max(0, len(topics) - TOPIC_CAP),
            'improved': improved, 'still_weak': [labels[key] for key in still],
            'revision_topics': [labels[key] for key in ordered_weak],
            'loose_ends': loose[:LOOSE_END_CAP], 'more_loose_ends': max(0, len(loose) - LOOSE_END_CAP)}


VOICES = {
    'new': 'Here is your study activity for this period.',
    'low': 'You recorded less study time than {basis}.',
    'steady': 'Your study time is in keeping with {basis}.',
    'heavy': 'You recorded more study time than {basis}. Nice work on the effort.',
    'rest': 'This was a heavier period than {basis}, with a change in your usual pattern.',
}


def recap_sections(payload):
    p = payload
    effort = (f"Last {p['days']} days: {p['minutes']} minutes across {p['sessions']} study sessions "
              f"on {p['active_days']} days.") if p['sessions'] else f"No sessions recorded in the last {p['days']} days: 0 minutes on 0 days."
    effort += ' ' + VOICES[p['pattern']].format(basis=p['basis'])
    if not p['coached']:
        learning = "You haven't logged any coached practice in this period — that's fine. Your recorded study effort is shown above."
    else:
        learning = 'Practised: ' + (', '.join(t[:40] for t in p['topics']) or 'your selected notes')
        if p['more_topics']:
            learning += f" (+{p['more_topics']} more)"
        learning += '.'
        if p['improved']:
            learning += ' Improved: ' + ', '.join(t[:40] for t in p['improved'][:3]) + '.'
            if len(p['improved']) > 3:
                learning += f" (+{len(p['improved']) - 3} more)"
        if p['still_weak']:
            learning += ' Still needing revision: ' + ', '.join(t[:40] for t in p['still_weak'][:3]) + '.'
            if len(p['still_weak']) > 3:
                learning += f" (+{len(p['still_weak']) - 3} more)"
        elif p['revision_topics']:
            learning += ' For revision: ' + ', '.join(t[:40] for t in p['revision_topics'][:3]) + '.'
        if not p['improved']:
            learning += ' No improvement established from saved assessment history.'
    loose = (f"{len(p['loose_ends']) + p['more_loose_ends']} unanswered Coach questions; resume below."
             if p['loose_ends'] else 'No unanswered Coach questions in this period.')
    if p['more_loose_ends']:
        loose += f" +{p['more_loose_ends']} older ones in your Sessions list."
    if p['pattern'] == 'rest':
        steps = ['Consider a lighter day or a break before your next study session.']
    elif p['revision_topics']:
        steps = [f"When you're ready, try one short revision session on {p['revision_topics'][0][:80]}."]
    elif p['loose_ends']:
        steps = ['When you have 10 minutes, resume the first unanswered question below.']
    elif p['has_sources']:
        steps = ['When you have five minutes, choose one topic from your notes for a Coach question.']
    else:
        steps = ['When you have five minutes, add one short note and start a Coach question.']
    return {'effort': effort, 'learning': learning, 'loose_ends': loose, 'next_step': steps}


def recap_section_options(payload):
    """Bounded wording alternatives keep model-selected phrasing fact-checked."""
    sections = recap_sections(payload)
    options = {key: [value] for key, value in sections.items()}
    alternate = {
        'new': 'These are the sessions you recorded in this period.',
        'low': 'Your recorded study time was below {basis}; you can pick things up when you are ready.',
        'steady': 'You kept a study rhythm close to {basis}.',
        'heavy': 'You put in more study time than {basis}. You can keep this pace if it suits you.',
        'rest': 'You studied more than {basis}, alongside a change in your usual pattern.',
    }[payload['pattern']].format(basis=payload['basis'])
    original = VOICES[payload['pattern']].format(basis=payload['basis'])
    options['effort'].append(sections['effort'].removesuffix(original) + alternate)
    if payload['pattern'] not in ('low', 'new', 'rest') and payload['revision_topics'] and payload['loose_ends']:
        options['next_step'].append(sections['next_step'] + [
            'Or spend 10 minutes resuming the first unanswered question below.'])
    return options


def render_sections(sections):
    return '\n\n'.join(title + '\n' + ('\n'.join(value) if isinstance(value, list) else value)
                         for title, value in zip(('Your effort', 'Your learning', 'Loose ends', 'Your next step'),
                                                  (sections[k] for k in ('effort', 'learning', 'loose_ends', 'next_step'))))


def split_recap_sections(text):
    """Read saved recap prose without changing older conversation turns."""
    if not text.startswith('Your effort\n'):
        return None
    rest = text[len('Your effort\n'):]
    sections = {}
    for key, heading in [('effort', 'Your learning'), ('learning', 'Loose ends'),
                         ('loose_ends', 'Your next step')]:
        if '\n\n' + heading + '\n' not in rest:
            return None
        sections[key], rest = rest.split('\n\n' + heading + '\n', 1)
    sections['next_step'] = rest
    return sections


def reconcile_recap(reply, payload):
    """Accept only complete, locally verified sentences; replace any unsafe section.

    Local facts are the authoritative vocabulary, including suggestions and tone.
    The provider can select/reorder those sentences, but cannot introduce facts,
    identifiers, instructions, or unsupported judgments in free prose.
    """
    local = recap_sections(payload)
    options = recap_section_options(payload)
    if not reply or reply.kind != 'recap' or reply.meta.get('pattern') != payload['pattern']:
        return local
    result = {}
    for key, value in local.items():
        candidate = reply.sections.get(key)
        # Exact verified clauses avoid accepting a true number in a false
        # relationship (e.g. swapping minutes and session counts).
        result[key] = candidate if candidate in options[key] else value
    return result
