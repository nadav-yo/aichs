"""Sync the native Windows title bar with the app theme (DWM immersive dark mode)."""

from __future__ import annotations

import os
import sys

_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_USE_IMMERSIVE_DARK_MODE_LEGACY = 19
_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWCP_ROUNDSMALL = 3

_filter_installed = False


def caption_prefers_dark(theme: str) -> bool:
    return theme == "dark"


def apply_windows_caption(widget, theme: str | None = None) -> None:
    if sys.platform != "win32":
        return

    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QDialog, QMainWindow, QWidget

    if not isinstance(widget, QWidget) or not widget.isWindow():
        return
    if not isinstance(widget, (QDialog, QMainWindow)):
        return
    if bool(widget.property("_aichsCustomChrome")):
        return
    if widget.windowFlags() & Qt.WindowType.FramelessWindowHint:
        return

    from ui.theme import current_theme

    name = theme or current_theme()
    dark = caption_prefers_dark(name)
    try:
        hwnd = int(widget.winId())
    except (AttributeError, TypeError, ValueError):
        return
    if hwnd == 0:
        return

    import ctypes

    value = ctypes.c_int(1 if dark else 0)
    dwmapi = ctypes.windll.dwmapi
    for attr in (_DWMWA_USE_IMMERSIVE_DARK_MODE, _DWMWA_USE_IMMERSIVE_DARK_MODE_LEGACY):
        if dwmapi.DwmSetWindowAttribute(
            hwnd,
            attr,
            ctypes.byref(value),
            ctypes.sizeof(value),
        ) == 0:
            break


def apply_windows_corner_preference(widget) -> None:
    if sys.platform != "win32":
        return
    if os.environ.get("QT_QPA_PLATFORM", "").lower() == "offscreen":
        return

    from PyQt6.QtWidgets import QDialog, QMainWindow, QWidget

    if not isinstance(widget, QWidget) or not widget.isWindow():
        return
    if not isinstance(widget, (QDialog, QMainWindow)):
        return
    if not bool(widget.property("_aichsCustomChrome")):
        return
    try:
        hwnd = int(widget.winId())
    except (AttributeError, TypeError, ValueError):
        return
    if hwnd == 0:
        return

    import ctypes

    value = ctypes.c_int(_DWMWCP_ROUNDSMALL)
    ctypes.windll.dwmapi.DwmSetWindowAttribute(
        hwnd,
        _DWMWA_WINDOW_CORNER_PREFERENCE,
        ctypes.byref(value),
        ctypes.sizeof(value),
    )


def sync_all_windows_captions(app, theme: str | None = None) -> None:
    if sys.platform != "win32":
        return
    for widget in app.topLevelWidgets():
        apply_windows_caption(widget, theme)


def install_caption_sync(app) -> None:
    global _filter_installed
    if sys.platform != "win32" or _filter_installed:
        return

    from PyQt6.QtCore import QEvent, QObject
    from PyQt6.QtWidgets import QDialog, QMainWindow, QWidget

    class _CaptionFilter(QObject):
        def eventFilter(self, obj, event):
            if (
                event.type() == QEvent.Type.Show
                and isinstance(obj, QWidget)
                and obj.isWindow()
                and isinstance(obj, (QDialog, QMainWindow))
            ):
                apply_windows_caption(obj)
            return False

    filt = _CaptionFilter(app)
    app.installEventFilter(filt)
    app._aichs_caption_filter = filt  # keep alive
    _filter_installed = True
