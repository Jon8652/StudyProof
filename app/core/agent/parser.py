"""Parse fenced or embedded JSON without executing model output."""
import json
from app.core.agent.schema import CoachReply, Quote
from app.constants import CONFIDENCE_LEVELS


def parse_reply(raw, expected_kind=None):
    if not isinstance(raw, str):
        raise ValueError('The AI reply was not readable.')
    decoder = json.JSONDecoder()
    value = None
    for i, char in enumerate(raw):
        if char == '{':
            try:
                value, _ = decoder.raw_decode(raw[i:])
                break
            except ValueError:
                continue
    if not isinstance(value, dict):
        raise ValueError('The AI reply was not readable.')
    kind, text = value.get('kind'), value.get('text')
    quotes, meta = value.get('quotes', []), value.get('meta', {})
    if kind not in ('question', 'hint', 'feedback', 'followup') or (expected_kind and kind != expected_kind):
        raise ValueError(f'kind must equal {expected_kind!r}.' if expected_kind
                         else 'kind must be question, hint, feedback or followup.')
    if not isinstance(text, str) or not text.strip():
        raise ValueError('text must be a non-empty string.')
    if len(text) > 2000:
        raise ValueError('text must contain at most 2000 characters.')
    if not isinstance(quotes, list) or len(quotes) > 5:
        raise ValueError('quotes must be a list containing at most 5 citations.')
    if not isinstance(meta, dict):
        raise ValueError('meta must be a JSON object.')
    if kind == 'feedback' and meta.get('confidence') not in CONFIDENCE_LEVELS:
        raise ValueError('The AI feedback must include a valid confidence rating.')
    parsed = []
    for i, q in enumerate(quotes):
        if (not isinstance(q, dict) or not isinstance(q.get('source_id'), str)
                or type(q.get('page')) is not int or q['page'] < 1
                or not isinstance(q.get('quote'), str) or not q['quote'].strip()):
            raise ValueError(f'quotes[{i}] must contain string source_id, positive integer page '
                             'and non-empty string quote.')
        parsed.append(Quote(q['source_id'], q['page'], q['quote']))
    return CoachReply(kind, text.strip(), parsed, meta)

