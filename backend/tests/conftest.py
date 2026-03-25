from __future__ import annotations

import pytest

from backend.app.core.settings import get_settings


@pytest.fixture
def isolated_data_dir(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("AI_NOVEL_DATA_DIR", str(data_dir))
    get_settings.cache_clear()
    try:
        yield data_dir
    finally:
        get_settings.cache_clear()
