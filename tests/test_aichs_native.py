from pathlib import Path

import aichs_native
from aichs_native.wheel_tags import platform_tag


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


def test_platform_tag_uses_default_without_release_override(monkeypatch):
    monkeypatch.delenv("AICHS_WHEEL_PLATFORM_TAG", raising=False)

    assert platform_tag("win_amd64") == "win_amd64"


def test_platform_tag_uses_release_override(monkeypatch):
    monkeypatch.setenv("AICHS_WHEEL_PLATFORM_TAG", "manylinux_2_28_x86_64")

    assert platform_tag("linux_x86_64") == "manylinux_2_28_x86_64"
