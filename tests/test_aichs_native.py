from pathlib import Path

import aichs_native


def test_binary_path_returns_only_installed_helpers(tmp_path, monkeypatch):
    monkeypatch.setattr(aichs_native, "__file__", str(tmp_path / "__init__.py"))

    assert aichs_native.binary_path("rg") is None

    binary = tmp_path / "bin" / "rg"
    binary.parent.mkdir()
    binary.write_text("", encoding="utf-8")

    assert aichs_native.binary_path("rg") == binary


def test_ripgrep_notice_is_distributed_with_native_helpers():
    notice = Path(aichs_native.__file__).with_name("THIRD_PARTY_NOTICES.md")

    assert "Ripgrep" in notice.read_text(encoding="utf-8")
    assert "Unlicense" in notice.read_text(encoding="utf-8")
