"""Lazy page lookup and citations preserving source casing."""
import re
from dataclasses import asdict


class PageIndex:
    def __init__(self, sources, notes, documents):
        self.sources = {s.id: s.kind for s in sources}
        self.notes, self.documents = notes, documents
        self.cache = {}

    def page(self, source_id, page):
        key = (source_id, page)
        if key not in self.cache:
            text = ''
            if self.sources.get(source_id) == 'note':
                note = self.notes.get(source_id)
                text = note.body if note and page == 1 else ''
            elif self.sources.get(source_id) == 'document':
                doc = self.documents.get(source_id)
                if doc:
                    text = next((p['text'] for p in doc.text_pages if p['page'] == page), '')
            self.cache[key] = re.sub(r'\s+', ' ', text).strip()
        return self.cache[key]


def validate_quotes(quotes, index, pack=None):
    valid, failures = [], 0
    for quote in quotes:
        q = asdict(quote) if not isinstance(quote, dict) else dict(quote)
        text = index.page(q['source_id'], q['page'])
        needle = re.sub(r'\s+', ' ', q['quote']).strip()
        match = re.search(re.escape(needle), text, re.IGNORECASE) if needle else None
        allowed = pack is None or any(
            s.source_id == q['source_id'] and s.page == q['page']
            and re.search(re.escape(needle), re.sub(r'\s+', ' ', s.text), re.IGNORECASE)
            for s in pack.spans)
        if match and allowed:
            q.update(quote=text[match.start():match.end()], verified=True)
            valid.append(q)
        else:
            failures += 1
    return valid, failures

