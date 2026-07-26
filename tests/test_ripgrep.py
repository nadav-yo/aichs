from pathlib import Path

import pytest

import services.ripgrep as ripgrep


def test_ripgrep_uses_bundled_binary_before_path(monkeypatch, tmp_path):
    bundled = tmp_path / "rg.exe"
    bundled.write_text("", encoding="utf-8")
    monkeypatch.setattr(ripgrep, "_bundled_candidates", lambda: (bundled,))
    monkeypatch.setattr(ripgrep.shutil, "which", lambda _name: "path-rg")

    assert ripgrep.ripgrep_path() == str(bundled)


def test_ripgrep_falls_back_to_path(monkeypatch):
    monkeypatch.setattr(ripgrep, "_bundled_candidates", lambda: ())
    monkeypatch.setattr(ripgrep.shutil, "which", lambda _name: "path-rg")

    assert ripgrep.ripgrep_path() == "path-rg"


def test_ripgrep_checks_platform_wheel_helpers(monkeypatch, tmp_path):
    wheel_binary = tmp_path / "rg.exe"
    wheel_binary.write_text("", encoding="utf-8")
    monkeypatch.setattr(ripgrep, "binary_path", lambda _name: wheel_binary)

    assert wheel_binary in ripgrep._bundled_candidates()


@pytest.mark.parametrize(("platform", "expected"), [("darwin", "macos"), ("linux", "linux")])
def test_ripgrep_platform_name(monkeypatch, platform, expected):
    monkeypatch.setattr(ripgrep.sys, "platform", platform)

    assert ripgrep._platform_name() == expected
