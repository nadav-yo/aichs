from pathlib import Path
import os
import re

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTextEdit, QComboBox, QWidget, QFileDialog, QScrollArea, QSlider,
    QListWidget, QListWidgetItem, QStackedWidget, QFrame, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QToolButton, QStyle,
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QIcon

from config import MODELS, SYSTEM_PROMPT
from services import model_registry
from services.model_registry import (
    api_key_env_var, get_model_config, get_provider_config, load_user_providers,
    save_user_providers,
)
from storage.settings import SettingsStore
from ui.avatars import avatar_pixmap, clear_cache, persist_portrait
from ui.theme import (
    ACCENT, palette, DEFAULT_COMPACTION_THRESHOLD_PCT, DEFAULT_FONT_SIZE, DEFAULT_THEME,
)

_NAV = [
    ("general", "General"),
    ("models", "Models"),
    ("chat", "Chat"),
    ("agent", "Agent"),
]

_BUILTIN_IDS = {"claude", "openai"}


def _provider_title(provider_id: str) -> str:
    if provider_id == "claude":
        return "Anthropic"
    if provider_id == "openai":
        return "OpenAI"
    return provider_id.replace("_", " ").replace("-", " ").title()


def _provider_env_var(provider_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", provider_id).strip("_").upper()
    return f"AICC_{cleaned}_API_KEY"


def _models_to_text(models: list[dict]) -> str:
    lines = []
    for model in models:
        mid = model.get("id", "")
        name = model.get("name", "")
        lines.append(f"{mid} = {name}" if name and name != mid else mid)
    return "\n".join(lines)


def _parse_models(text: str) -> list[dict]:
    models = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if "=" in line:
            mid, name = [part.strip() for part in line.split("=", 1)]
        elif "|" in line:
            mid, name = [part.strip() for part in line.split("|", 1)]
        else:
            mid, name = line, ""
        if mid:
            item = {"id": mid}
            if name:
                item["name"] = name
            models.append(item)
    return models


class _PortraitPicker(QWidget):
    def __init__(self, role: str, label: str, saved: str, styles: dict, parent=None):
        super().__init__(parent)
        self._role = role
        self._default = role
        self._custom_path: str | None = None
        self._styles = styles
        p = palette()

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)

        self.preview = QLabel()
        self.preview.setFixedSize(48, 48)
        self.preview.setStyleSheet(f"border:1px solid {p['BORDER']}; border-radius:24px;")

        col = QVBoxLayout()
        col.setSpacing(4)
        title = QLabel(label)
        title.setStyleSheet(f"color:{p['TEXT']}; font-size:13px; font-weight:bold;")
        col.addWidget(title)

        self.status = QLabel()
        self.status.setStyleSheet(styles["hint"])
        col.addWidget(self.status)

        controls = QHBoxLayout()
        controls.setSpacing(6)
        browse = QPushButton("Choose image…")
        browse.setStyleSheet(styles["btn"])
        browse.clicked.connect(self._browse)
        reset = QPushButton("Use default")
        reset.setStyleSheet(styles["btn"])
        reset.clicked.connect(self._reset)
        controls.addWidget(browse)
        controls.addWidget(reset)
        controls.addStretch()
        col.addLayout(controls)

        row.addWidget(self.preview)
        row.addLayout(col, 1)

        if saved and Path(saved).is_file():
            self._custom_path = saved
        self._refresh()

    def _refresh(self):
        source = self._custom_path or self._default
        self.preview.setPixmap(
            avatar_pixmap(source, 48).scaled(
                48, 48, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        if self._custom_path:
            self.status.setText(f"Custom · {Path(self._custom_path).name}")
        else:
            self.status.setText("Built-in default")

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose portrait", "",
            "Images (*.png *.jpg *.jpeg *.gif *.webp *.bmp *.svg)",
        )
        if path:
            self._custom_path = path
            self._refresh()

    def _reset(self):
        self._custom_path = None
        self._refresh()

    def value(self) -> str:
        return self._custom_path or self._default


class _ProviderDialog(QDialog):
    def __init__(self, styles: dict, existing_ids: set[str], data: dict | None = None,
                 parent=None):
        super().__init__(parent)
        self._styles = styles
        self._existing_ids = existing_ids
        self._data = data or {}
        self._original_id = (data or {}).get("id", "")
        self.setWindowTitle("Provider")
        self.resize(480, 520)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        p = palette()
        self.kind = QComboBox()
        self.kind.addItem("Anthropic", "anthropic")
        self.kind.addItem("OpenAI", "openai")
        self.kind.addItem("Custom", "custom")
        self.kind.setStyleSheet(styles["field"])
        self.kind.currentIndexChanged.connect(self._apply_kind_defaults)
        self._field(root, "Provider type", self.kind)

        self.provider_id = QLineEdit()
        self.provider_id.setStyleSheet(styles["field"])
        self._field(root, "Provider ID", self.provider_id)

        self.base_url = QLineEdit()
        self.base_url.setPlaceholderText("Optional for built-ins, required for most custom providers")
        self.base_url.setStyleSheet(styles["field"])
        self._field(root, "Base URL", self.base_url)

        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setStyleSheet(styles["field"])
        self._field(root, "API key", self.api_key)

        self.models = QTextEdit()
        self.models.setPlaceholderText("model-id\nmodel-id = Display Name")
        self.models.setMinimumHeight(120)
        self.models.setStyleSheet(styles["field"])
        self._field(root, "Models", self.models)

        hint = QLabel("For custom providers, models are saved to ~/.aicc/models.json. API keys stay in settings.")
        hint.setWordWrap(True)
        hint.setStyleSheet(styles["hint"])
        root.addWidget(hint)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("Cancel")
        cancel.setStyleSheet(styles["btn"])
        cancel.clicked.connect(self.reject)
        add = QPushButton("Save")
        add.setStyleSheet(
            f"QPushButton {{ background:{ACCENT}; color:white; border:none;"
            "border-radius:6px; padding:6px 18px; font-weight:bold; }"
            f"QPushButton:hover {{ background:#0066d6; }}"
        )
        add.clicked.connect(self._accept_if_valid)
        buttons.addWidget(cancel)
        buttons.addWidget(add)
        root.addLayout(buttons)

        if data:
            kind = data.get("kind", "custom")
            idx = self.kind.findData(kind)
            if idx >= 0:
                self.kind.setCurrentIndex(idx)
            self.provider_id.setText(data.get("id", ""))
            self.base_url.setText(data.get("base_url", ""))
            self.api_key.setText(data.get("api_key", ""))
            self.models.setPlainText(_models_to_text(data.get("models", [])))
        else:
            self._apply_kind_defaults()

        self.setStyleSheet(f"QDialog {{ background:{p['BG2']}; }}")

    def _field(self, layout: QVBoxLayout, label: str, widget: QWidget):
        lbl = QLabel(label)
        lbl.setStyleSheet(self._styles["label"])
        layout.addWidget(lbl)
        layout.addWidget(widget)

    def _apply_kind_defaults(self):
        if self._original_id:
            return
        kind = self.kind.currentData()
        if kind == "anthropic":
            self.provider_id.setText("claude")
            self.base_url.clear()
            self.models.setPlainText(_models_to_text(_builtin_models("claude")))
        elif kind == "openai":
            self.provider_id.setText("openai")
            self.base_url.clear()
            self.models.setPlainText(_models_to_text(_builtin_models("openai")))
        else:
            self.provider_id.setText("")
            self.base_url.clear()
            self.models.clear()

    def _accept_if_valid(self):
        provider_id = self.provider_id.text().strip()
        if not provider_id:
            QMessageBox.warning(self, "Provider ID required", "Enter a provider ID.")
            return
        if provider_id in self._existing_ids and provider_id != self._original_id:
            QMessageBox.warning(self, "Provider exists", "That provider is already configured.")
            return
        if not _parse_models(self.models.toPlainText()):
            QMessageBox.warning(self, "Models required", "Add at least one model.")
            return
        self.accept()

    def value(self) -> dict:
        kind = self.kind.currentData()
        provider_id = self.provider_id.text().strip()
        if kind == "anthropic":
            api = "anthropic"
            api_key_spec = self._data.get("api_key_spec") or "ANTHROPIC_API_KEY"
        elif kind == "openai":
            api = "openai-compatible"
            api_key_spec = self._data.get("api_key_spec") or "OPENAI_API_KEY"
        else:
            api = "openai-compatible"
            api_key_spec = self._data.get("api_key_spec") or _provider_env_var(provider_id)
        return {
            "id": provider_id,
            "kind": kind,
            "api": api,
            "base_url": self.base_url.text().strip(),
            "api_key": self.api_key.text().strip(),
            "api_key_spec": api_key_spec,
            "models": _parse_models(self.models.toPlainText()),
        }


def _builtin_models(provider: str) -> list[dict]:
    models = []
    for model_id in MODELS.get(provider, []):
        name = get_model_config(model_id).display_name
        item = {"id": model_id}
        if name and name != model_id:
            item["name"] = name
        models.append(item)
    return models


def _scroll_page(content: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setWidget(content)
    return scroll


class SettingsDialog(QDialog):
    def __init__(self, store: SettingsStore, parent=None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("Settings")
        self.resize(640, 480)
        self.setMinimumSize(560, 420)

        p = palette()
        field_base = (
            f"background:{p['BG3']}; color:{p['TEXT']}; border:1px solid {p['BORDER']};"
            "border-radius:6px; padding:8px 10px; font-size:13px;"
        )
        self._field_style = (
            f"QLineEdit, QTextEdit, QComboBox {{ {field_base} }}"
            "QComboBox::drop-down { border:none; width:22px; }"
            "QComboBox::down-arrow { image:none; }"
            f"QComboBox QAbstractItemView {{ background:{p['BG3']}; color:{p['TEXT']};"
            f"border:1px solid {p['BORDER']}; border-radius:6px;"
            f"selection-background-color:{p['SELECTION']};"
            f"selection-color:{p['SELECTION_TEXT']}; outline:none; padding:4px; }}"
            f"QComboBox QAbstractItemView::item {{ background:{p['BG3']};"
            f"color:{p['TEXT']}; padding:6px 8px; }}"
            f"QComboBox QAbstractItemView::item:selected {{ background:{p['SELECTION']};"
            f"color:{p['SELECTION_TEXT']}; }}"
        )
        self._label_style = f"color:{p['TEXT_DIM']}; font-size:12px;"
        self._hint_style = f"color:{p['TEXT_DIM']}; font-size:12px;"
        self._btn_style = (
            f"QPushButton {{ background:{p['BG3']}; color:{p['TEXT_DIM']};"
            f"border:1px solid {p['BORDER']}; border-radius:6px; padding:4px 12px; font-size:12px; }}"
            f"QPushButton:hover {{ color:{p['TEXT']}; background:{p['BORDER']}; }}"
        )
        self._icon_btn_style = (
            f"QToolButton {{ background:{p['BG3']}; color:{p['TEXT_DIM']};"
            f"border:1px solid {p['BORDER']}; border-radius:6px; padding:3px; }}"
            f"QToolButton:hover {{ color:{p['TEXT']}; background:{p['BORDER']}; }}"
        )
        self._styles = {
            "hint": self._hint_style,
            "btn": self._btn_style,
            "field": self._field_style,
            "label": self._label_style,
        }
        self._providers: list[dict] = []
        self._default_combos: dict[str, QComboBox] = {}

        saved = store.load()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        # ── left nav ──────────────────────────────────────────────────────
        self._nav = QListWidget()
        self._nav.setFixedWidth(148)
        self._nav.setSpacing(0)
        self._nav.setStyleSheet(
            f"QListWidget {{ background:{p['BG']}; border:none; border-right:1px solid {p['BORDER']};"
            f"padding:8px 6px; outline:none; }}"
            f"QListWidget::item {{ color:{p['TEXT_DIM']}; padding:10px 12px 10px 15px; border-radius:6px;"
            f"border-left:3px solid transparent; }}"
            f"QListWidget::item:selected {{ background:{p['BG3']}; color:{p['TEXT']};"
            f"border-left:3px solid {ACCENT}; }}"
            f"QListWidget::item:hover:!selected {{ background:{p['BG2']}; }}"
        )
        for _id, title in _NAV:
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, _id)
            item.setSizeHint(QSize(0, 36))
            self._nav.addItem(item)
        self._nav.currentRowChanged.connect(self._on_nav)

        # ── pages ─────────────────────────────────────────────────────────
        self._stack = QStackedWidget()
        self._stack.setStyleSheet(f"background:{p['BG2']};")

        self._stack.addWidget(_scroll_page(self._page_general()))
        self._stack.addWidget(_scroll_page(self._page_models()))
        self._stack.addWidget(_scroll_page(self._page_chat(saved)))
        self._stack.addWidget(_scroll_page(self._page_agent()))

        body.addWidget(self._nav)
        body.addWidget(self._stack, 1)
        outer.addLayout(body, 1)

        # ── footer ────────────────────────────────────────────────────────
        footer = QFrame()
        footer.setStyleSheet(
            f"QFrame {{ background:{p['BG2']}; border-top:1px solid {p['BORDER']}; }}"
        )
        buttons = QHBoxLayout(footer)
        buttons.setContentsMargins(16, 10, 16, 10)

        buttons.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save")
        save.setStyleSheet(
            f"QPushButton {{ background:{ACCENT}; color:white; border:none;"
            f"border-radius:6px; padding:6px 20px; font-weight:bold; }}"
            f"QPushButton:hover {{ background:#0066d6; }}"
        )
        save.clicked.connect(self._save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        outer.addWidget(footer)

        self._load_values(saved)
        self._nav.setCurrentRow(0)

    # ── page builders ─────────────────────────────────────────────────────

    def _page_shell(self, title: str, subtitle: str = "") -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 24)
        layout.setSpacing(16)
        p = palette()
        hdr = QLabel(title)
        hdr.setStyleSheet(f"color:{p['TEXT']}; font-size:18px; font-weight:bold;")
        layout.addWidget(hdr)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setWordWrap(True)
            sub.setStyleSheet(self._hint_style)
            layout.addWidget(sub)
        return page, layout

    def _field(self, layout: QVBoxLayout, label: str, widget: QWidget):
        lbl = QLabel(label)
        lbl.setStyleSheet(self._label_style)
        layout.addWidget(lbl)
        layout.addWidget(widget)

    def _section_separator(self) -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"background:{palette()['BORDER']}; max-height:1px;")
        return sep

    def _page_general(self) -> QWidget:
        page, layout = self._page_shell(
            "General",
            "Look and feel of the app.",
        )
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["dark", "modern", "light"])
        self.theme_combo.setStyleSheet(self._field_style)
        self._field(layout, "Theme", self.theme_combo)

        self.font_combo = QComboBox()
        self.font_combo.addItems(["small", "medium", "large"])
        self.font_combo.setStyleSheet(self._field_style)
        self._field(layout, "Chat font size", self.font_combo)
        layout.addStretch()
        return page

    def _page_models(self) -> QWidget:
        page, layout = self._page_shell(
            "Models",
            "Configured providers and default models.",
        )

        row = QHBoxLayout()
        row.addStretch()
        add_btn = QPushButton("+ Add provider")
        add_btn.setStyleSheet(self._btn_style)
        add_btn.clicked.connect(self._add_provider)
        row.addWidget(add_btn)
        layout.addLayout(row)

        self.providers_table = QTableWidget(0, 5)
        self.providers_table.setHorizontalHeaderLabels([
            "Provider", "Type", "Endpoint", "Default", "",
        ])
        self.providers_table.verticalHeader().setVisible(False)
        self.providers_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.providers_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.providers_table.setAlternatingRowColors(False)
        self.providers_table.setStyleSheet(
            f"QTableWidget {{ background:{palette()['BG2']}; color:{palette()['TEXT']};"
            f"border:1px solid {palette()['BORDER']}; border-radius:8px; gridline-color:{palette()['BORDER']}; }}"
            f"QHeaderView::section {{ background:{palette()['BG3']}; color:{palette()['TEXT_DIM']};"
            f"border:none; border-bottom:1px solid {palette()['BORDER']}; padding:6px; }}"
        )
        header = self.providers_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.providers_table)

        layout.addWidget(self._section_separator())

        compact_lbl = QLabel("Compaction threshold")
        compact_lbl.setStyleSheet(self._label_style)
        layout.addWidget(compact_lbl)
        self.compaction_label = QLabel()
        self.compaction_label.setWordWrap(True)
        self.compaction_label.setStyleSheet(self._hint_style)
        layout.addWidget(self.compaction_label)
        self.compaction_slider = QSlider(Qt.Orientation.Horizontal)
        self.compaction_slider.setRange(60, 95)
        self.compaction_slider.setSingleStep(5)
        self.compaction_slider.setPageStep(5)
        self.compaction_slider.valueChanged.connect(self._update_compaction_label)
        layout.addWidget(self.compaction_slider)
        layout.addStretch()
        return page

    def _load_configured_providers(self, saved: dict) -> list[dict]:
        configured = []
        user_providers = load_user_providers()
        seen = set()
        for provider_id, raw in user_providers.items():
            configured.append(self._provider_row(provider_id, saved, raw))
            seen.add(provider_id)
        for provider_id in _BUILTIN_IDS - seen:
            if self._has_builtin_key(provider_id, saved):
                configured.append(self._provider_row(provider_id, saved, None))
        return configured

    def _provider_row(self, provider_id: str, saved: dict, raw: dict | None) -> dict:
        cfg = get_provider_config(provider_id)
        api = (raw or {}).get("api") or (cfg.api if cfg else "openai-compatible")
        if provider_id == "claude":
            kind = "anthropic"
        elif provider_id == "openai":
            kind = "openai"
        else:
            kind = "custom"
        models = []
        if raw and raw.get("models"):
            for item in raw.get("models", []):
                models.append(dict(item))
        else:
            models = _builtin_models(provider_id)
        api_key_spec = (
            (raw or {}).get("apiKey")
            or (raw or {}).get("api_key_spec")
            or (cfg.api_key_spec if cfg else _provider_env_var(provider_id))
        )
        return {
            "id": provider_id,
            "kind": kind,
            "api": api,
            "base_url": (raw or {}).get("baseUrl", (raw or {}).get("base_url", cfg.base_url if cfg else "")) or "",
            "api_key": self._saved_provider_key(saved, provider_id),
            "api_key_spec": api_key_spec,
            "models": models,
        }

    def _saved_provider_key(self, saved: dict, provider_id: str) -> str:
        provider_keys = saved.get("provider_api_keys", {})
        if provider_id in provider_keys:
            return str(provider_keys.get(provider_id, ""))
        if provider_id == "claude":
            return str(saved.get("anthropic_api_key", ""))
        if provider_id == "openai":
            return str(saved.get("openai_api_key", ""))
        return ""

    def _has_builtin_key(self, provider_id: str, saved: dict) -> bool:
        if self._saved_provider_key(saved, provider_id).strip():
            return True
        cfg = get_provider_config(provider_id)
        env_var = api_key_env_var(cfg.api_key_spec) if cfg else None
        return bool(env_var and os.environ.get(env_var))

    def _refresh_provider_table(self, defaults: dict | None = None):
        defaults = defaults or self.store.load().get("default_models", {})
        self._default_combos = {}
        self.providers_table.setRowCount(0)
        for row_idx, provider in enumerate(self._providers):
            self.providers_table.insertRow(row_idx)
            self.providers_table.setItem(row_idx, 0, QTableWidgetItem(_provider_title(provider["id"])))
            self.providers_table.setItem(row_idx, 1, QTableWidgetItem(provider["kind"].replace("-", " ")))
            self.providers_table.setItem(row_idx, 2, QTableWidgetItem(provider.get("base_url") or "Default"))

            combo = QComboBox()
            combo.setStyleSheet(self._field_style)
            models = provider.get("models", [])
            combo.setToolTip(f"{len(models)} configured model{'s' if len(models) != 1 else ''}")
            combo.setMinimumWidth(180)
            for model in models:
                mid = model.get("id", "")
                name = model.get("name", mid)
                combo.addItem(name if name == mid else f"{name} ({mid})", mid)
            model = defaults.get(provider["id"], self._default_model_for_row(provider))
            idx = combo.findData(model)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            self._default_combos[provider["id"]] = combo
            self.providers_table.setCellWidget(row_idx, 3, combo)

            actions = QWidget()
            buttons = QHBoxLayout(actions)
            buttons.setContentsMargins(0, 0, 0, 0)
            buttons.setSpacing(4)
            edit = self._icon_button("Edit provider", "document-edit", QStyle.StandardPixmap.SP_FileDialogDetailedView)
            edit.clicked.connect(lambda _, i=row_idx: self._edit_provider(i))
            trash_fallback = getattr(
                QStyle.StandardPixmap,
                "SP_TrashIcon",
                QStyle.StandardPixmap.SP_DialogDiscardButton,
            )
            remove = self._icon_button("Remove provider", "edit-delete", trash_fallback)
            remove.clicked.connect(lambda _, i=row_idx: self._remove_provider(i))
            buttons.addWidget(edit)
            buttons.addWidget(remove)
            self.providers_table.setCellWidget(row_idx, 4, actions)
        self.providers_table.resizeRowsToContents()

    def _icon_button(self, tooltip: str, theme_icon: str, fallback: QStyle.StandardPixmap) -> QToolButton:
        button = QToolButton()
        icon = QIcon.fromTheme(theme_icon)
        if icon.isNull():
            icon = self.style().standardIcon(fallback)
        button.setIcon(icon)
        button.setIconSize(QSize(14, 14))
        button.setFixedSize(QSize(28, 28))
        button.setToolTip(tooltip)
        button.setStyleSheet(self._icon_btn_style)
        return button

    def _add_provider(self):
        dialog = _ProviderDialog(
            self._styles,
            {provider["id"] for provider in self._providers},
            parent=self,
        )
        if dialog.exec():
            self._providers.append(dialog.value())
            self._refresh_provider_table()

    def _edit_provider(self, row: int):
        if row < 0 or row >= len(self._providers):
            return
        existing = {provider["id"] for provider in self._providers}
        dialog = _ProviderDialog(self._styles, existing, self._providers[row], self)
        if dialog.exec():
            self._providers[row] = dialog.value()
            self._refresh_provider_table()

    def _remove_provider(self, row: int):
        if row < 0 or row >= len(self._providers):
            return
        del self._providers[row]
        self._refresh_provider_table()

    def _default_model_for_row(self, provider: dict) -> str:
        models = provider.get("models", [])
        if provider.get("id") == "claude" and len(models) > 1:
            return models[1].get("id", "")
        if models:
            return models[0].get("id", "")
        return ""

    def _page_chat(self, saved: dict) -> QWidget:
        page, layout = self._page_shell(
            "Chat",
            "Portraits shown beside your messages and the agent's replies.",
        )
        self.human_portrait = _PortraitPicker(
            "human", "You", saved.get("avatar_human", "human"), self._styles,
        )
        self.agent_portrait = _PortraitPicker(
            "agent", "Agent", saved.get("avatar_agent", "agent"), self._styles,
        )
        layout.addWidget(self.human_portrait)
        layout.addWidget(self.agent_portrait)
        layout.addStretch()
        return page

    def _page_agent(self) -> QWidget:
        page, layout = self._page_shell(
            "Agent",
            "Overrides SYSTEM_PROMPT from config.py. Workspace context is still appended automatically.",
        )
        row = QHBoxLayout()
        row.addStretch()
        reset_btn = QPushButton("Reset to default")
        reset_btn.setStyleSheet(self._btn_style)
        reset_btn.clicked.connect(lambda: self.system_prompt.setPlainText(SYSTEM_PROMPT))
        row.addWidget(reset_btn)
        layout.addLayout(row)

        self.system_prompt = QTextEdit()
        self.system_prompt.setStyleSheet(self._field_style)
        self.system_prompt.setMinimumHeight(200)
        layout.addWidget(self.system_prompt, 1)
        return page

    # ── logic ─────────────────────────────────────────────────────────────

    def _on_nav(self, row: int):
        if row >= 0:
            self._stack.setCurrentIndex(row)

    def _load_values(self, saved: dict):
        self.system_prompt.setPlainText(saved.get("system_prompt", SYSTEM_PROMPT))

        defaults = saved.get("default_models", {})
        self._providers = self._load_configured_providers(saved)
        self._refresh_provider_table(defaults)

        theme = saved.get("theme", DEFAULT_THEME)
        if theme in ("dark", "modern", "light"):
            self.theme_combo.setCurrentText(theme)

        font = saved.get("font_size", DEFAULT_FONT_SIZE)
        if font in ("small", "medium", "large"):
            self.font_combo.setCurrentText(font)

        pct = saved.get("compaction_threshold_pct", DEFAULT_COMPACTION_THRESHOLD_PCT)
        try:
            pct = int(pct)
        except (TypeError, ValueError):
            pct = DEFAULT_COMPACTION_THRESHOLD_PCT
        self.compaction_slider.setValue(max(60, min(95, pct)))
        self._update_compaction_label(self.compaction_slider.value())

    def _update_compaction_label(self, pct: int):
        claude_tokens = int(180_000 * pct / 100) - 16_384
        openai_tokens = int(100_000 * pct / 100) - 16_384
        self.compaction_label.setText(
            f"Compact when a conversation exceeds {pct}% of the context window "
            f"(~{claude_tokens:,} tokens for Claude, ~{openai_tokens:,} for OpenAI)."
        )

    def _save(self):
        data = self.store.load()
        configured_ids = {provider["id"] for provider in self._providers}
        provider_keys = {
            provider["id"]: provider.get("api_key", "").strip()
            for provider in self._providers
        }
        default_models = {}
        for provider_id, combo in self._default_combos.items():
            default_models[provider_id] = combo.currentData() or combo.currentText()

        user_providers = {}
        for provider in self._providers:
            provider_id = provider["id"]
            is_builtin = provider_id in _BUILTIN_IDS
            has_override = bool(provider.get("base_url")) or not is_builtin
            if not has_override:
                continue
            entry = {
                "api": provider.get("api", "openai-compatible"),
                "apiKey": provider.get("api_key_spec") or _provider_env_var(provider_id),
            }
            if provider.get("base_url"):
                entry["baseUrl"] = provider["base_url"]
            if not is_builtin:
                entry["models"] = provider.get("models", [])
            user_providers[provider_id] = entry

        data.update({
            "anthropic_api_key": provider_keys.get("claude", ""),
            "openai_api_key": provider_keys.get("openai", ""),
            "provider_api_keys": provider_keys,
            "system_prompt": self.system_prompt.toPlainText().strip() or SYSTEM_PROMPT,
            "theme": self.theme_combo.currentText(),
            "font_size": self.font_combo.currentText(),
            "compaction_threshold_pct": self.compaction_slider.value(),
            "default_models": default_models,
            "avatar_human": persist_portrait(self.human_portrait.value(), "human"),
            "avatar_agent": persist_portrait(self.agent_portrait.value(), "agent"),
        })
        if not configured_ids:
            data["default_models"] = {}
            data["provider_api_keys"] = {}
            data["anthropic_api_key"] = ""
            data["openai_api_key"] = ""
        save_user_providers(user_providers)
        model_registry.reload()
        self.store.save(data)
        self.store.apply_saved(data)
        clear_cache()
        self.accept()
