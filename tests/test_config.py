from pathlib import Path

from config import resolve_aichs_home


def test_resolve_aichs_home_uses_env_override(monkeypatch, tmp_path):
    custom_home = tmp_path / "custom-home"
    monkeypatch.setenv("AICHS_HOME", str(custom_home))

    assert resolve_aichs_home() == custom_home


def test_resolve_aichs_home_defaults_to_user_home(monkeypatch, tmp_path):
    fake_home = tmp_path / "user-home"
    monkeypatch.delenv("AICHS_HOME", raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

    assert resolve_aichs_home() == fake_home / ".aichs"
