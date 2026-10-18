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
