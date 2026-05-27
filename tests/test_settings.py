import json
import os

import pytest

from storage.settings import SettingsStore


@pytest.fixture
def settings_store(isolate_aichs_home):
    return SettingsStore()


def test_load_save_roundtrip(settings_store, isolate_aichs_home):
    settings_store.save({"theme": "light", "font_size": "large"})
    data = settings_store.load()
    assert data["theme"] == "light"
    assert data["font_size"] == "large"


def test_update_merges(settings_store):
    settings_store.save({"theme": "dark"})
    merged = settings_store.update({"font_size": "medium"})
    assert merged["theme"] == "dark"
    assert merged["font_size"] == "medium"


def test_apply_sets_env_when_missing(settings_store, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings_store.save({"provider_api_keys": {"claude": "test-key-123"}})
    settings_store.apply()
    assert os.environ.get("ANTHROPIC_API_KEY") == "test-key-123"


def test_apply_saved_overwrites_env(settings_store, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "old")
    settings_store.apply_saved({"provider_api_keys": {"openai": "new-key"}})
    assert os.environ["OPENAI_API_KEY"] == "new-key"


def test_load_invalid_json_returns_empty(settings_store, isolate_aichs_home):
    from config import SETTINGS_PATH

    SETTINGS_PATH.write_text("{not json", encoding="utf-8")
    assert settings_store.load() == {}
