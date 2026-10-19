"""Parse fenced or embedded JSON without executing model output."""
import json
from app.core.agent.schema import CoachReply, Quote


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
    if (kind not in ('question', 'hint', 'feedback', 'followup')
            or (expected_kind and kind != expected_kind)
            or not isinstance(text, str) or not text.strip()
            or len(text) > 2000 or not isinstance(quotes, list)
            or len(quotes) > 5 or not isinstance(meta, dict)):
        raise ValueError('The AI reply did not match the requested format.')
    parsed = []
    for q in quotes:
        if (not isinstance(q, dict) or not isinstance(q.get('source_id'), str)
                or type(q.get('page')) is not int or q['page'] < 1
                or not isinstance(q.get('quote'), str) or not q['quote'].strip()):
            raise ValueError('The AI citation was not readable.')
        parsed.append(Quote(q['source_id'], q['page'], q['quote']))
    return CoachReply(kind, text.strip(), parsed, meta)

