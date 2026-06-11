from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QPushButton, QWidget

from services.tool_registry import (
    extension_panel_data,
    extension_status_badges,
)
from ui.theme import ACCENT, meta_font_pt, palette, tone_badge_button_style
from ui.widgets.extension_panel_dialog import ExtensionPanelDialog


class _ExtensionBadgeSignals(QObject):
    done = pyqtSignal(int, object, object, str)


class _ExtensionBadgeWorker(QRunnable):
    def __init__(
        self,
        generation: int,
        cwd: str,
        model: str,
        history: list[dict],
    ):
        super().__init__()
        self.signals = _ExtensionBadgeSignals()
        self._generation = generation
        self._cwd = cwd
        self._model = model
        self._history = history

    def run(self) -> None:
        try:
            badges, errors = extension_status_badges(
                self._cwd,
                model=self._model,
                history=self._history,
            )
        except BaseException as exc:
            self.signals.done.emit(self._generation, [], [], str(exc))
            return
        self.signals.done.emit(self._generation, badges, errors, "")


class ExtensionContributionsBar(QWidget):
    def __init__(
        self,
        cwd: str,
        *,
        on_action: Callable[[dict], None] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._cwd = cwd
        self._model = ""
        self._history: list[dict] = []
        self._on_action = on_action
        self._badge_generation = 0
        self._badge_active = False
        self._badge_errors: list[str] = []
        self._badge_pool = QThreadPool(self)
        self._badge_pool.setMaxThreadCount(1)

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)

        self._badges = QWidget()
        self._badges_layout = QHBoxLayout(self._badges)
        self._badges_layout.setContentsMargins(0, 0, 0, 0)
        self._badges_layout.setSpacing(4)
        self._layout.addWidget(self._badges)
        self._badges.setVisible(False)
        self.setVisible(False)

        self.refresh()

    def set_context(self, *, cwd: str, model: str, history: list[dict]) -> None:
        self._cwd = cwd
        self._model = model
        self._history = [dict(item) for item in history]
        self.refresh()

    def refresh(self) -> list[str]:
        self._badge_generation += 1
        generation = self._badge_generation
        self._badge_active = True
        worker = _ExtensionBadgeWorker(
            generation,
            self._cwd,
            self._model,
            [dict(item) for item in self._history],
        )
        worker.signals.done.connect(self._on_badges_done)
        self._badge_pool.start(worker)
        return list(self._badge_errors)

    def _on_badges_done(self, generation: int, badges, errors, error: str) -> None:
        if generation != self._badge_generation:
            return
        self._badge_active = False
        self._badge_errors = [str(item) for item in (errors or [])]
        if error:
            self._badge_errors.append(error)
            badges = []
        self._render_badges(list(badges or []))

    def _render_badges(self, badges) -> None:
        self._clear_badges()
        for badge, raw in badges:
            data = _badge_data(badge.name, raw)
            if data is None:
                continue
            button = QPushButton(data.label)
            button.setToolTip(data.tooltip)
            button.setFixedHeight(28)
            button.setProperty("aichs-tone", data.tone)
            button.setStyleSheet(_badge_style(data.tone))
            button.clicked.connect(lambda _, panel=data.panel: self._open_panel(panel))
            self._badges_layout.addWidget(button)

        has_badges = self._badges_layout.count() > 0
        self._badges.setVisible(has_badges)
        self.setVisible(has_badges)

    def apply_appearance(self) -> None:
        for i in range(self._badges_layout.count()):
            widget = self._badges_layout.itemAt(i).widget()
            if isinstance(widget, QPushButton):
                widget.setStyleSheet(_badge_style(str(widget.property("aichs-tone") or "")))

    def _clear_badges(self) -> None:
        while self._badges_layout.count():
            item = self._badges_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _open_panel(self, name: str) -> None:
        def load_panel():
            panel_title, panel_data, _panel_errors = extension_panel_data(
                self._cwd,
                name,
                model=self._model,
                history=self._history,
            )
            return panel_title, panel_data

        dialog = ExtensionPanelDialog(
            name,
            {"title": name, "body": "Loading panel..."},
            on_action=self._on_action,
            parent=self.window(),
        )
        dialog.set_refresh_callback(load_panel)
        dialog.refresh_panel()
        dialog.exec()


@dataclass(frozen=True)
class _BadgeData:
    label: str
    tooltip: str
    tone: str
    panel: str


def _badge_data(name: str, raw) -> _BadgeData | None:
    if isinstance(raw, str):
        label = raw.strip()
        if not label:
            return None
        return _BadgeData(label=label, tooltip="", tone="", panel=name)

    if not isinstance(raw, dict):
        label = str(raw).strip()
        if not label:
            return None
        return _BadgeData(label=label, tooltip="", tone="", panel=name)

    if raw.get("visible") is False:
        return None
    label = str(raw.get("label") or "").strip()
    if not label:
        return None
    return _BadgeData(
        label=label,
        tooltip=str(raw.get("tooltip") or ""),
        tone=str(raw.get("tone") or ""),
        panel=str(raw.get("panel") or name),
    )


def _badge_style(tone: str = "") -> str:
    return tone_badge_button_style(tone)
