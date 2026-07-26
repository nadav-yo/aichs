from pathlib import Path

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
