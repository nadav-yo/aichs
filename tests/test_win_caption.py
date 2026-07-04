import sys
from unittest.mock import MagicMock

import pytest

from ui.win_caption import (
    apply_windows_caption,
    apply_windows_corner_preference,
    caption_prefers_dark,
)


@pytest.mark.parametrize(
    "theme,expected",
    [
        ("dark", True),
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


def test_apply_windows_corner_preference_skips_non_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    widget = MagicMock(isWindow=lambda: True)

    apply_windows_corner_preference(widget)

    widget.winId.assert_not_called()


def test_apply_windows_corner_preference_skips_non_custom_window(monkeypatch, qapp):
    import ctypes

    from PyQt6.QtWidgets import QMainWindow

    class FakeDwmApi:
        def __init__(self):
            self.calls = []

        def DwmSetWindowAttribute(self, *args):
            self.calls.append(args)
            return 0

    fake_dwmapi = FakeDwmApi()
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.setattr(ctypes, "windll", MagicMock(dwmapi=fake_dwmapi), raising=False)
    widget = QMainWindow()
    widget.show()
    fake_dwmapi.calls.clear()

    apply_windows_corner_preference(widget)

    assert fake_dwmapi.calls == []


def test_apply_windows_corner_preference_skips_offscreen_platform(monkeypatch, qapp):
    import ctypes

    from PyQt6.QtWidgets import QMainWindow

    class FakeDwmApi:
        def __init__(self):
            self.calls = []

        def DwmSetWindowAttribute(self, *args):
            self.calls.append(args)
            return 0

    fake_dwmapi = FakeDwmApi()
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(ctypes, "windll", MagicMock(dwmapi=fake_dwmapi), raising=False)
    widget = QMainWindow()
    widget.setProperty("_aichsCustomChrome", True)
    widget.show()

    apply_windows_corner_preference(widget)

    assert fake_dwmapi.calls == []


def test_apply_windows_corner_preference_calls_dwm_for_custom_windows(monkeypatch, qapp):
    import ctypes

    from PyQt6.QtWidgets import QDialog, QMainWindow

    class FakeDwmApi:
        def __init__(self):
            self.calls = []

        def DwmSetWindowAttribute(self, *args):
            self.calls.append(args)
            return 0

    fake_dwmapi = FakeDwmApi()
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.setattr(ctypes, "windll", MagicMock(dwmapi=fake_dwmapi), raising=False)

    for widget_cls in (QMainWindow, QDialog):
        widget = widget_cls()
        widget.setProperty("_aichsCustomChrome", True)
        widget.show()

        apply_windows_corner_preference(widget)

    assert len(fake_dwmapi.calls) == 2
    assert {call[1] for call in fake_dwmapi.calls} == {33}