"""Shared test fixtures.

Redirects all app-data paths into a temporary directory so persistence
tests never touch the real user folder (Part C test plan).
"""

from __future__ import annotations

import gc
import tkinter as tk

import pytest

from app.utils import paths, resources


@pytest.fixture(autouse=True)
def isolated_gui_runtime(monkeypatch):
    """Keep Tk images and partially built roots within their own test.

    Cached CTkImages contain PhotoImages owned by one Tcl interpreter and
    cannot be reused after that test's root is destroyed.
    """
    destroy = tk.Tk.destroy

    def destroy_without_pending_callbacks(root):
        # CTk's appearance/DPI timers can otherwise outlive a destroyed
        # root and run while the next test initializes its interpreter.
        for job in root.tk.splitlist(root.tk.call("after", "info")):
            # Let each owning widget delete its callback command on destroy.
            root.tk.call("after", "cancel", job)
        destroy(root)

    monkeypatch.setattr(tk.Tk, "destroy", destroy_without_pending_callbacks)
    resources._IMAGE_CACHE.clear()
    yield
    root = tk._default_root
    if root is not None:
        try:
            root.destroy()
        except tk.TclError:
            pass
    resources._IMAGE_CACHE.clear()
    gc.collect()


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
