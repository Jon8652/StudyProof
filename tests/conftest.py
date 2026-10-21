"""Shared test fixtures.

Redirects all app-data paths into a temporary directory so persistence
tests never touch the real user folder (Part C test plan).
"""

from __future__ import annotations

import pytest

from app.utils import paths


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Point every app-data path helper at a per-test temp directory."""
    data_dir = tmp_path / "appdata"
    data_dir.mkdir()
    paths.set_override_data_dir(data_dir)
    yield data_dir
    paths.set_override_data_dir(None)


class ScriptedProvider:
    """Fixed replies for exercising the coach boundary without network calls."""
    confidence = 'high'

    def __init__(self, settings):
        pass

    def generate(self, context):
        from app.core.agent.schema import CoachReply, Quote
        span = context['pack'].spans[0]
        kind = context['kind']
        quotes = [Quote(span.source_id, span.page, span.text)] if kind in ('hint', 'feedback') else []
        return CoachReply(kind, 'Confidence: high. Explain how sunlight supplies energy.',
                          quotes, {'confidence': self.confidence} if kind == 'feedback' else {})

    def assess_answer(self, context):
        from app.core.agent.schema import AnswerAssessment
        span = context['pack'].spans[0]
        return AnswerAssessment(span.source_id, 'Light and energy', context['question'].prompt,
                                {'low': 30, 'medium': 75, 'high': 95}[self.confidence])


@pytest.fixture
def scripted_provider():
    return ScriptedProvider
