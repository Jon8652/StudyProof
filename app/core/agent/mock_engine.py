"""Deterministic extractive offline coach."""
import hashlib
import random
import re
from app.core.agent.schema import CoachReply, Quote


def keywords(text):
    return {w.lower() for w in re.findall(r'\b\w{6,}\b', text)}


class MockProvider:
    def generate(self, context):
        pack, kind = context['pack'], context['kind']
        question = context.get('question')
        candidates = [(s, t.strip()) for s in pack.spans
                      for t in re.split(r'(?<=[.!?])\s+', s.text) if t.strip()]
        if not candidates:
            raise ValueError('Select sources containing study text first.')
        answer = (question.revisions[-1]['text'] if question and question.revisions
                  else question.first_answer or '') if question else ''
        missing = keywords(' '.join(t for _, t in candidates)) - keywords(answer)
        if question and question.answer_key.get('sentence'):
            missing = keywords(question.answer_key['sentence']) - keywords(answer)
        if kind in ('question', 'followup'):
            preferred = [(s, t) for s, t in candidates if 80 <= len(t) <= 200]
            if kind == 'followup':
                previous = question.answer_key.get('sentence', '')
                next_sentences = [(s, t) for s, t in candidates
                                  if t != previous and keywords(t) & missing]
                preferred = next_sentences or [(s, t) for s, t in candidates if keywords(t) & missing] or preferred
            seed = hashlib.sha256(f"{context['session_id']}:{context['counter']}".encode()).digest()
            span, sentence = random.Random(seed).choice(preferred or candidates)
            if len(sentence) > 200:
                sentence = sentence[:200].rsplit(' ', 1)[0]
            words = list(re.finditer(r'\b\w+\b', sentence))
            eligible = [w for w in words[1:-1] if len(w.group()) >= 6]
            term = max(eligible, key=lambda w: len(w.group()), default=None)
            prompt = (sentence[:term.start()] + '____' + sentence[term.end():]
                      + '\nWhich term completes this?') if term else 'Explain this idea in your own words: ' + sentence
            return CoachReply(kind, prompt[:2000], [], {'answer_key': {
                'term': term.group() if term else '', 'sentence': sentence,
                'source_id': span.source_id, 'page': span.page}})
        key = question.answer_key
        sentence = key.get('sentence') or (question.spans[0]['text'] if question.spans else candidates[0][1])
        source_id = key.get('source_id') or candidates[0][0].source_id
        page = key.get('page') or candidates[0][0].page
        matched = keywords(sentence) & keywords(answer)
        absent = keywords(sentence) - keywords(answer)
        text = sentence if kind == 'hint' else (
            ('Matched ideas: ' + ', '.join(sorted(matched)) if matched else 'No match yet in your answer.')
            + '\nMissing ideas to explain: ' + (', '.join(sorted(absent)) or 'none; explain the connections in your own words.'))
        return CoachReply(kind, text[:2000], [Quote(source_id, page, sentence)])

