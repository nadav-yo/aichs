import sys
from unittest.mock import MagicMock

import pytest

from ui.win_caption import apply_windows_caption, caption_prefers_dark


@pytest.mark.parametrize(
    "theme,expected",
    [
        ("dark", True),
        ("modern", True),
        ("light", False),
    ],
)
def test_caption_prefers_dark(theme, expected):
    assert caption_prefers_dark(theme) is expected


def test_apply_windows_caption_skips_non_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    widget = MagicMock(isWindow=lambda: True)
    apply_windows_caption(widget)
    widget.winId.assert_not_called()


def test_apply_windows_caption_skips_non_window(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    widget = MagicMock(isWindow=lambda: False)
    apply_windows_caption(widget)
    widget.winId.assert_not_called()


def test_apply_windows_caption_skips_non_qwidget_window_like_object(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    widget = MagicMock(isWindow=lambda: True)
    apply_windows_caption(widget)
    widget.winId.assert_not_called()


def test_apply_windows_caption_skips_plain_qwidget_window(monkeypatch, qapp):
    from PyQt6.QtWidgets import QWidget

    monkeypatch.setattr(sys, "platform", "win32")
    widget = QWidget()
    widget.setWindowTitle("Plain")
    widget.show()

    apply_windows_caption(widget)

    assert widget.isWindow()
