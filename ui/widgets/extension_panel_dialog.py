from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ui.theme import palette, chat_font_pt, meta_font_pt


class ExtensionPanelDialog(QDialog):
    def __init__(self, title: str, data, *, on_action: Callable[[dict], None] | None = None, parent=None):
        super().__init__(parent)
        self._on_action = on_action
        self._on_refresh: Callable[[], tuple[str, object]] | None = None
        self._data = data
        self._warnings: list[str] = []
        self.setWindowTitle(title)
        self.resize(560, 520)

        p = palette()
        self.setStyleSheet(
            f"QDialog {{ background:{p['BG2']}; color:{p['TEXT']}; }}"
            f"QScrollArea {{ background:{p['BG2']}; border:none; }}"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        self._heading = QLabel(_panel_title(title, data))
        self._heading.setStyleSheet(
            f"font-size:{chat_font_pt() + 2}px; font-weight:600; color:{p['TEXT']};"
        )
        root.addWidget(self._heading)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        self._layout = QVBoxLayout(body)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(10)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        self._render(data)
        self._render_warnings()
        self._layout.addStretch()

    def _render(self, data):
        if isinstance(data, str):
            self._add_body(data)
            return
        if not isinstance(data, dict):
            self._add_body(str(data))
            return
        _warn_unknown_keys(self._warnings, "panel", data, {"title", "body", "sections"})

        body = data.get("body")
        if body:
            self._add_body(str(body))

        for section in data.get("sections", []) or []:
            self._add_section(section)

    def _add_section(self, section):
        if isinstance(section, str):
            self._add_body(section)
            return
        if not isinstance(section, dict):
            self._add_body(str(section))
            return
        _warn_unknown_keys(self._warnings, "section", section, {"heading", "body", "items"})

        heading = section.get("heading")
        if heading:
            label = QLabel(str(heading))
            label.setStyleSheet(_heading_style())
            self._layout.addWidget(label)

        body = section.get("body")
        if body:
            self._add_body(str(body))

        for item in section.get("items", []) or []:
            self._add_item(item)

    def _add_item(self, item):
        p = palette()
        card = QFrame()
        card.setObjectName("extensionPanelItem")
        card.setStyleSheet(
            f"QFrame#extensionPanelItem {{ background-color:{p['BG3']};"
            f"border:1px solid {p['BORDER']}; border-radius:8px; }}"
        )
        layout = QHBoxLayout(card)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(10)

        if isinstance(item, str):
            title = item
            subtitle = ""
            body = ""
            actions = []
        else:
            if not isinstance(item, dict):
                item = {"title": str(item)}
            _warn_unknown_keys(
                self._warnings,
                "item",
                item,
                {"title", "subtitle", "body", "action", "actions"},
            )
            title = str(item.get("title", "Item"))
            subtitle = str(item.get("subtitle", ""))
            body = str(item.get("body", ""))
            actions = _normalise_actions(item.get("actions"), self._warnings)
            action = item.get("action")
            if action:
                actions.extend(_normalise_actions(action, self._warnings))

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(3)

        title_label = QLabel(title)
        title_label.setWordWrap(True)
        title_label.setStyleSheet(f"color:{p['TEXT']}; font-weight:600;")
        text_col.addWidget(title_label)

        if subtitle:
            sub = QLabel(subtitle)
            sub.setWordWrap(True)
            sub.setStyleSheet(f"color:{p['TEXT_DIM']}; font-size:{meta_font_pt()}px;")
            text_col.addWidget(sub)

        if body:
            body_label = QLabel(body)
            body_label.setWordWrap(True)
            body_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            body_label.setStyleSheet(f"color:{p['TEXT']};")
            text_col.addWidget(body_label)

        layout.addLayout(text_col, 1)

        if actions and self._on_action:
            actions_col = QVBoxLayout()
            actions_col.setContentsMargins(0, 0, 0, 0)
            actions_col.setSpacing(6)
            for action in actions:
                if not isinstance(action, dict):
                    continue
                if not _is_supported_action(action):
                    self._warnings.append(
                        f"Unsupported action type: {action.get('type') or 'missing'}"
                    )
                    continue
                label = str(action.get("label") or action.get("type") or "Action")
                btn = QPushButton(label)
                btn.setStyleSheet(_action_button_style())
                btn.clicked.connect(lambda _, a=dict(action): self._run_action(a))
                actions_col.addWidget(btn)
            actions_col.addStretch()
            layout.addLayout(actions_col)

        self._layout.addWidget(card)

    def _render_warnings(self):
        if not self._warnings:
            return
        unique = []
        for warning in self._warnings:
            if warning not in unique:
                unique.append(warning)
        self._add_section({
            "heading": "Panel warnings",
            "items": unique,
        })

    def _run_action(self, action: dict):
        if action.get("type") == "refresh_panel":
            self._refresh()
            return
        if self._on_action:
            self._on_action(action)

    def _refresh(self):
        if self._on_refresh:
            title, data = self._on_refresh()
            self._data = data
            self._heading.setText(_panel_title(title, data))
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                _delete_layout(item.layout())
        self._warnings.clear()
        self._render(self._data)
        self._render_warnings()
        self._layout.addStretch()

    def set_refresh_callback(self, callback: Callable[[], tuple[str, object]]) -> None:
        self._on_refresh = callback

    def _add_body(self, text: str):
        p = palette()
        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setStyleSheet(f"color:{p['TEXT']};")
        self._layout.addWidget(label)


def _panel_title(default: str, data) -> str:
    if isinstance(data, dict) and data.get("title"):
        return str(data["title"])
    return default


def _heading_style() -> str:
    p = palette()
    return (
        f"color:{p['TEXT_DIM']}; font-size:{meta_font_pt()}px;"
        "font-weight:600;"
    )


def _normalise_actions(raw, warnings: list[str]) -> list[dict]:
    if raw is None:
        return []
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        valid = [item for item in raw if isinstance(item, dict)]
        skipped = len(raw) - len(valid)
        if skipped:
            warnings.append(f"Ignored {skipped} malformed action(s).")
        return valid
    warnings.append("Ignored malformed action data.")
    return []


def _warn_unknown_keys(
    warnings: list[str],
    label: str,
    data: dict,
    allowed: set[str],
) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        warnings.append(f"Ignored unsupported {label} field(s): {', '.join(unknown)}")


def _is_supported_action(action: dict) -> bool:
    return action.get("type") in {"open_file", "copy", "refresh_panel", "send_message"}


def _delete_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            _delete_layout(item.layout())


def _action_button_style() -> str:
    p = palette()
    return (
        f"QPushButton {{ background-color:{p['BG2']}; color:{p['TEXT']};"
        f"border:1px solid {p['BORDER']}; border-radius:7px;"
        f"padding:4px 10px; font-size:{meta_font_pt()}px; min-width:52px; }}"
        f"QPushButton:hover {{ background-color:{p['BORDER']}; }}"
    )
