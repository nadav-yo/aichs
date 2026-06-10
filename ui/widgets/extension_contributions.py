from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PyQt6.QtWidgets import QHBoxLayout, QPushButton, QWidget

from services.tool_registry import (
    extension_panel_data,
    extension_status_badges,
)
from ui.theme import ACCENT, meta_font_pt, palette
from ui.widgets.extension_panel_dialog import ExtensionPanelDialog


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

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)

        self._badges = QWidget()
        self._badges_layout = QHBoxLayout(self._badges)
        self._badges_layout.setContentsMargins(0, 0, 0, 0)
        self._badges_layout.setSpacing(4)
        self._layout.addWidget(self._badges)

        self.refresh()

    def set_context(self, *, cwd: str, model: str, history: list[dict]) -> None:
        self._cwd = cwd
        self._model = model
        self._history = history
        self.refresh()

    def refresh(self) -> list[str]:
        self._clear_badges()
        badges, errors = extension_status_badges(
            self._cwd,
            model=self._model,
            history=self._history,
        )
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
        return errors

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

        title, data, _errors = extension_panel_data(
            self._cwd,
            name,
            model=self._model,
            history=self._history,
        )
        dialog = ExtensionPanelDialog(
            title,
            data,
            on_action=self._on_action,
            parent=self.window(),
        )
        dialog.set_refresh_callback(load_panel)
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
    p = palette()
    colors = {
        "success": (p["SUCCESS_BG"], p["SUCCESS"], p["SUCCESS_BORDER"]),
        "danger": ("#35191d", "#f87171", "#5f252d"),
        "warning": ("#32260f", "#fbbf24", "#5a4319"),
        "accent": ("#172341", ACCENT, "#2d477c"),
    }
    bg, fg, border = colors.get(tone, (p["BG3"], p["TEXT_DIM"], p["BORDER"]))
    return (
        f"QPushButton {{ background-color:{bg}; color:{fg}; border:1px solid {border};"
        "border-radius:8px; padding-left:8px; padding-right:8px;"
        f"font-size:{meta_font_pt()}px; }}"
        f"QPushButton:hover {{ color:{p['TEXT']}; border-color:{p['TEXT_DIM']}; }}"
    )
