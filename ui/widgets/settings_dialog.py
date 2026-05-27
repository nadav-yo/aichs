from pathlib import Path
import os
import re

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTextEdit, QComboBox, QWidget, QFileDialog, QScrollArea, QSlider,
    QListWidget, QListWidgetItem, QStackedWidget, QFrame, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QToolButton, QStyle, QCheckBox,
    QColorDialog, QTabWidget, QAbstractItemView,
)
from PyQt6.QtCore import Qt, QSize, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap

from config import MODELS, SYSTEM_PROMPT
from services import model_registry
from services.crew import all_crew, crew_settings
from services.model_registry import (
    api_default_context_window,
    api_key_env_var,
    context_window_tokens,
    custom_default_context_window,
    get_model_config,
    get_provider_config,
    load_user_providers,
    save_user_providers,
)
from storage.settings import SettingsStore
from ui.avatars import avatar_pixmap, clear_cache, persist_portrait
from ui.theme import (
    ACCENT, palette, DEFAULT_FONT_SIZE, DEFAULT_THEME,
    apply_flat_tab_style, crew_tone, separator_color,
)

_NAV = [
    ("general", "General"),
    ("models", "Models"),
    ("crew", "Crew"),
]

_BUILTIN_IDS = {"claude", "openai"}
_MODEL_CONTEXT_SUFFIX = re.compile(r"\s@\s*(\d+)\s*$")
_CUSTOM_DEFAULT_CONTEXT = 32_768


def _provider_title(provider_id: str) -> str:
    if provider_id == "claude":
        return "Anthropic"
    if provider_id == "openai":
        return "OpenAI"
    return provider_id.replace("_", " ").replace("-", " ").title()


def _provider_env_var(provider_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", provider_id).strip("_").upper()
    return f"AICHS_{cleaned}_API_KEY"


def _model_context_window(model: dict) -> int | None:
    raw = model.get("contextWindow", model.get("context_window"))
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _models_to_text(models: list[dict], *, include_context: bool = False) -> str:
    lines = []
    for model in models:
        mid = model.get("id", "")
        name = model.get("name", "")
        line = f"{mid} = {name}" if name and name != mid else mid
        if include_context:
            window = _model_context_window(model)
            if window:
                line = f"{line} @ {window}"
        lines.append(line)
    return "\n".join(lines)


def _parse_models(text: str) -> list[dict]:
    models = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        window = None
        ctx_match = _MODEL_CONTEXT_SUFFIX.search(line)
        if ctx_match:
            window = int(ctx_match.group(1))
            line = line[:ctx_match.start()].strip()
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
            if window:
                item["contextWindow"] = window
            models.append(item)
    return models


def _model_ids(models: list[dict]) -> list[str]:
    return [str(model.get("id", "")) for model in models if model.get("id")]


def _drag_handle_icon() -> QIcon:
    p = palette()
    pix = QPixmap(16, 16)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(p["TEXT_DIM"]), 1.6))
    for y in (5, 8, 11):
        painter.drawLine(4, y, 12, y)
    painter.end()
    return QIcon(pix)


class _ReorderableProviderTable(QTableWidget):
    row_moved = pyqtSignal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_row = -1
        self.setDragEnabled(False)
        self.setAcceptDrops(False)
        self.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_row = self.rowAt(int(event.position().y()))
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_row >= 0 and event.buttons() & Qt.MouseButton.LeftButton:
            row = self.rowAt(int(event.position().y()))
            if row >= 0:
                self.selectRow(row)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        source = self._drag_row
        self._drag_row = -1
        dest = self.rowAt(int(event.position().y()))
        if dest < 0:
            dest = self.rowCount() - 1
        if event.button() == Qt.MouseButton.LeftButton and source >= 0 and dest >= 0 and source != dest:
            self.row_moved.emit(source, dest)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _ModelOrderList(QListWidget):
    order_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragDropOverwriteMode(False)
        self.setDropIndicatorShown(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setAlternatingRowColors(False)
        self.setMinimumHeight(132)

    def dropEvent(self, event):
        super().dropEvent(event)
        self.order_changed.emit()


class _PortraitPicker(QWidget):
    def __init__(
        self,
        role: str,
        label: str,
        saved: str,
        styles: dict,
        parent=None,
        default_source: str | None = None,
        accent_color: str = "",
    ):
        super().__init__(parent)
        self._role = role
        self._default = default_source or role
        self._custom_path: str | None = None
        self._accent_color = _clean_color(accent_color)
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
        border = self._accent_color or palette()["BORDER"]
        self.preview.setStyleSheet(f"border:2px solid {border}; border-radius:24px;")
        self.preview.setPixmap(
            avatar_pixmap(source, 48, self._accent_color).scaled(
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

    def set_accent_color(self, color: str):
        self._accent_color = _clean_color(color)
        self._refresh()


class _ColorSwatchButton(QPushButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._color = "#528bff"
        self._fg = "#ffffff"
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(36)
        self.setMinimumWidth(104)
        self.setToolTip("Choose crew color")

    def set_color(self, color: str):
        self._color = _clean_color(color) or "#528bff"
        self._fg = _contrast_text(self._color)
        self.setText(self._color)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        painter.setBrush(QColor(self._color))
        border = palette()["TEXT_DIM"] if self.underMouse() or self.hasFocus() else self._color
        painter.setPen(QPen(QColor(border), 1))
        painter.drawRoundedRect(rect, 8, 8)

        font = QFont(self.font())
        font.setBold(True)
        font.setPointSize(12)
        painter.setFont(font)
        painter.setPen(QColor(self._fg))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.text())
        painter.end()


class _ColorPicker(QWidget):
    color_changed = pyqtSignal(str)

    def __init__(self, saved: str, fallback: str, styles: dict, parent=None):
        super().__init__(parent)
        self._value = _clean_color(saved)
        self._fallback = _clean_color(fallback) or "#528bff"
        self._styles = styles
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        self.swatch = _ColorSwatchButton()
        self.swatch.clicked.connect(self._pick)
        row.addWidget(self.swatch)

        self.reset = QPushButton()
        self.reset.setText("Reset")
        self.reset.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reset.setToolTip("Use default crew color")
        self.reset.setFixedHeight(36)
        self.reset.setStyleSheet(self._styles["btn"])
        self.reset.clicked.connect(self._reset)
        row.addWidget(self.reset)
        self._refresh()

    def _pick(self):
        color = QColorDialog.getColor(QColor(self.display_color()), self)
        if color.isValid():
            self._value = color.name()
            self._refresh()
            self.color_changed.emit(self.display_color())

    def _reset(self):
        self._value = ""
        self._refresh()
        self.color_changed.emit(self.display_color())

    def _refresh(self):
        display = self.display_color()
        self.swatch.set_color(display)
        self.reset.setEnabled(bool(self._value))

    def display_color(self) -> str:
        return self._value or self._fallback

    def value(self) -> str:
        return self._value


def _clean_color(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if not text.startswith("#"):
        text = f"#{text}"
    return text if re.fullmatch(r"#[0-9a-fA-F]{6}", text) else ""


def _contrast_text(hex_color: str) -> str:
    color = _clean_color(hex_color)
    if not color:
        return "#ffffff"
    r = int(color[1:3], 16)
    g = int(color[3:5], 16)
    b = int(color[5:7], 16)
    # Perceived luminance; dark text reads better on bright crew swatches.
    luminance = (0.299 * r + 0.587 * g + 0.114 * b)
    return "#111111" if luminance > 160 else "#ffffff"


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

        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(styles["hint"])
        root.addWidget(self.hint)

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
            self._apply_kind_ui(kind)
            self.models.setPlainText(
                _models_to_text(
                    data.get("models", []),
                    include_context=kind == "custom",
                ),
            )
        else:
            self._apply_kind_defaults()

        self.setStyleSheet(f"QDialog {{ background:{p['BG2']}; }}")

    def _apply_kind_ui(self, kind: str):
        if kind == "custom":
            self.models.setPlaceholderText(
                "llama3.1:8b = Llama 3.1 8B @ 32768\n"
                "qwen2.5-coder:7b @ 65536"
            )
            self.hint.setText(
                "For custom providers (e.g. Ollama), append @ tokens to each model line "
                f"(defaults to {custom_default_context_window():,} when omitted). "
                "Models are saved to ~/.aichs/models.json; API keys stay in settings."
            )
        else:
            self.models.setPlaceholderText("model-id\nmodel-id = Display Name")
            if kind == "anthropic":
                self.hint.setText(
                    "Anthropic context limits are fetched from the API when a key is available."
                )
            else:
                self.hint.setText(
                    "OpenAI context limits use built-in defaults. "
                    "Models are saved to ~/.aichs/models.json; API keys stay in settings."
                )

    def _field(self, layout: QVBoxLayout, label: str, widget: QWidget):
        lbl = QLabel(label)
        lbl.setStyleSheet(self._styles["label"])
        layout.addWidget(lbl)
        layout.addWidget(widget)

    def _apply_kind_defaults(self):
        kind = self.kind.currentData()
        self._apply_kind_ui(kind)
        if self._original_id:
            return
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


def _provider_compaction_example(provider: dict) -> str:
    provider_id = provider["id"]
    kind = provider.get("kind", "custom")
    models = provider.get("models", [])
    if kind == "anthropic":
        if models:
            windows = sorted({
                context_window_tokens(model["id"])
                for model in models
                if model.get("id")
            })
            if len(windows) == 1:
                return f"{provider_id}: ~{_compaction_limit(windows[0]):,}"
            if windows:
                return (
                    f"{provider_id}: ~{_compaction_limit(windows[0]):,}"
                    f"–{_compaction_limit(windows[-1]):,}"
                )
        return f"{provider_id}: ~{_compaction_limit(api_default_context_window('anthropic')):,}"
    if kind == "openai":
        return f"{provider_id}: ~{_compaction_limit(api_default_context_window('openai-compatible')):,}"
    windows = sorted({
        _model_context_window(model) or custom_default_context_window()
        for model in models
    })
    if not windows:
        return f"{provider_id}: ~{_compaction_limit(custom_default_context_window()):,}"
    if len(windows) == 1:
        return f"{provider_id}: ~{_compaction_limit(windows[0]):,}"
    return (
        f"{provider_id}: ~{_compaction_limit(windows[0]):,}"
        f"–{_compaction_limit(windows[-1]):,}"
    )


def _compaction_limit(window: int) -> int:
    from services.compaction import compaction_threshold

    return compaction_threshold(window)


def _parse_context_window_value(value) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _apply_legacy_provider_context(models: list[dict], provider_window: int | None) -> list[dict]:
    if not provider_window:
        return models
    migrated = []
    for model in models:
        item = dict(model)
        if not _model_context_window(item):
            item["contextWindow"] = provider_window
        migrated.append(item)
    return migrated


def _builtin_models(provider: str) -> list[dict]:
    models = []
    for model_id in MODELS.get(provider, []):
        name = get_model_config(model_id).display_name
        item = {"id": model_id}
        if name and name != model_id:
            item["name"] = name
        models.append(item)
    return models


def _has_builtin_model_order_override(provider_id: str, models: list[dict]) -> bool:
    if provider_id not in _BUILTIN_IDS:
        return False
    builtin_models = model_registry._BUILTIN.get(provider_id, {}).get("models", [])
    return _model_ids(models) != _model_ids(builtin_models)


def _apply_provider_order(providers: list[dict], saved: dict) -> list[dict]:
    order = saved.get("provider_order", [])
    if not isinstance(order, list):
        return providers
    by_id = {provider["id"]: provider for provider in providers}
    ordered = []
    seen = set()
    for provider_id in order:
        provider = by_id.get(str(provider_id))
        if provider and provider["id"] not in seen:
            ordered.append(provider)
            seen.add(provider["id"])
    ordered.extend(provider for provider in providers if provider["id"] not in seen)
    return ordered


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
        self._model_order_provider_row = -1
        self._crew_widgets: dict[str, dict] = {}

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
            f"QListWidget {{ background:{p['BG']}; border:none; border-right:1px solid {separator_color()};"
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

        self._stack.addWidget(_scroll_page(self._page_general(saved)))
        self._stack.addWidget(_scroll_page(self._page_models()))
        self._stack.addWidget(_scroll_page(self._page_crew(saved)))

        body.addWidget(self._nav)
        body.addWidget(self._stack, 1)
        outer.addLayout(body, 1)

        # ── footer ────────────────────────────────────────────────────────
        footer = QFrame()
        footer.setStyleSheet(
            f"QFrame {{ background:{p['BG2']}; border-top:1px solid {separator_color()}; }}"
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
        sep_color = separator_color()
        sep.setStyleSheet(
            f"background:{sep_color}; color:{sep_color}; border:none; max-height:1px;"
        )
        return sep

    def _page_general(self, saved: dict) -> QWidget:
        page, layout = self._page_shell(
            "General",
            "Look, feel, and composer behavior.",
        )
        p = palette()
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["dark", "modern", "light"])
        self.theme_combo.setStyleSheet(self._field_style)
        self._field(layout, "Theme", self.theme_combo)

        self.font_combo = QComboBox()
        self.font_combo.addItems(["small", "medium", "large"])
        self.font_combo.setStyleSheet(self._field_style)
        self._field(layout, "Chat font size", self.font_combo)

        layout.addWidget(self._section_separator())

        check_icon = (Path(__file__).resolve().parents[2] / "assets" / "checkmark.svg").as_posix()
        self.enter_to_send_check = QCheckBox("Enter sends message")
        self.enter_to_send_check.setStyleSheet(
            f"QCheckBox {{ color:{p['TEXT']}; font-size:13px; spacing:8px; }}"
            f"QCheckBox::indicator {{ width:16px; height:16px;"
            f"background:{p['BG3']}; border:1px solid {p['TEXT_DIM']}; border-radius:3px; }}"
            f"QCheckBox::indicator:hover {{ border:1px solid {ACCENT}; }}"
            f"QCheckBox::indicator:checked {{ image:url({check_icon}); border:1px solid {ACCENT}; }}"
        )
        layout.addWidget(self.enter_to_send_check)

        enter_hint = QLabel("When enabled, Shift+Enter inserts a new line.")
        enter_hint.setWordWrap(True)
        enter_hint.setStyleSheet(self._hint_style)
        layout.addWidget(enter_hint)

        layout.addWidget(self._section_separator())
        self.human_portrait = _PortraitPicker(
            "human", "Your avatar", saved.get("avatar_human", "human"), self._styles,
        )
        layout.addWidget(self.human_portrait)
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

        self.providers_table = _ReorderableProviderTable()
        self.providers_table.setColumnCount(5)
        self.providers_table.setHorizontalHeaderLabels([
            "", "Provider", "Type", "Endpoint", "",
        ])
        self.providers_table.verticalHeader().setVisible(False)
        self.providers_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.providers_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.providers_table.setAlternatingRowColors(False)
        self.providers_table.itemSelectionChanged.connect(self._on_provider_selection_changed)
        self.providers_table.row_moved.connect(self._move_provider)
        self.providers_table.setStyleSheet(
            f"QTableWidget {{ background:{palette()['BG2']}; color:{palette()['TEXT']};"
            f"border:1px solid {separator_color()}; border-radius:8px; gridline-color:{palette()['BORDER']}; }}"
            f"QHeaderView::section {{ background:{palette()['BG3']}; color:{palette()['TEXT_DIM']};"
            f"border:none; border-bottom:1px solid {separator_color()}; padding:6px; }}"
        )
        header = self.providers_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(0, 30)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.providers_table)

        order_lbl = QLabel("Model order")
        order_lbl.setStyleSheet(self._label_style)
        layout.addWidget(order_lbl)

        order_hint = QLabel("Drag models to reorder them. The first model is used on startup unless a saved chat model exists.")
        order_hint.setWordWrap(True)
        order_hint.setStyleSheet(self._hint_style)
        layout.addWidget(order_hint)

        self.model_order_list = _ModelOrderList()
        self.model_order_list.setStyleSheet(
            f"QListWidget {{ background:{palette()['BG2']}; color:{palette()['TEXT']};"
            f"border:1px solid {palette()['BORDER']}; border-radius:8px; padding:4px; outline:none; }}"
            f"QListWidget::item {{ padding:8px 10px; border-radius:6px; }}"
            f"QListWidget::item:selected {{ background:{palette()['SELECTION']}; color:{palette()['SELECTION_TEXT']}; }}"
        )
        self.model_order_list.order_changed.connect(self._apply_model_order)
        layout.addWidget(self.model_order_list)

        layout.addWidget(self._section_separator())

        compact_lbl = QLabel("Auto-compaction")
        compact_lbl.setStyleSheet(self._label_style)
        layout.addWidget(compact_lbl)
        self.compaction_label = QLabel()
        self.compaction_label.setWordWrap(True)
        self.compaction_label.setStyleSheet(self._hint_style)
        layout.addWidget(self.compaction_label)

        reserve_row = QHBoxLayout()
        reserve_lbl = QLabel("Response reserve")
        reserve_lbl.setStyleSheet(self._label_style)
        reserve_row.addWidget(reserve_lbl)
        self.compaction_reserve_edit = QLineEdit()
        self.compaction_reserve_edit.setPlaceholderText("Auto (scaled from context window)")
        self.compaction_reserve_edit.textChanged.connect(self._update_compaction_label)
        reserve_row.addWidget(self.compaction_reserve_edit, 1)
        layout.addLayout(reserve_row)

        keep_row = QHBoxLayout()
        keep_lbl = QLabel("Recent history kept")
        keep_lbl.setStyleSheet(self._label_style)
        keep_row.addWidget(keep_lbl)
        self.compaction_keep_recent_edit = QLineEdit()
        self.compaction_keep_recent_edit.setPlaceholderText("Auto (scaled from context window)")
        self.compaction_keep_recent_edit.textChanged.connect(self._update_compaction_label)
        keep_row.addWidget(self.compaction_keep_recent_edit, 1)
        layout.addLayout(keep_row)

        self._update_compaction_label()
        layout.addStretch()
        return page

    def _load_configured_providers(self, saved: dict) -> list[dict]:
        configured = []
        user_providers = load_user_providers()
        seen = set()
        for provider_id, raw in user_providers.items():
            configured.append(self._provider_row(provider_id, saved, raw))
            seen.add(provider_id)
        for provider_id in ("claude", "openai"):
            if provider_id in seen:
                continue
            if self._has_builtin_key(provider_id, saved):
                configured.append(self._provider_row(provider_id, saved, None))
        return _apply_provider_order(configured, saved)

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
        provider_window = _parse_context_window_value(
            (raw or {}).get("contextWindow", (raw or {}).get("context_window")),
        )
        if kind == "custom":
            models = _apply_legacy_provider_context(models, provider_window)
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
        selected_row = self.providers_table.currentRow()
        if selected_row < 0:
            selected_row = 0
        self.providers_table.blockSignals(True)
        self.providers_table.setRowCount(0)
        for row_idx, provider in enumerate(self._providers):
            self.providers_table.insertRow(row_idx)
            grip = QTableWidgetItem()
            grip.setIcon(_drag_handle_icon())
            grip.setToolTip("Drag to reorder providers")
            self._set_provider_item(row_idx, 0, grip)
            self._set_provider_item(row_idx, 1, QTableWidgetItem(_provider_title(provider["id"])))
            self._set_provider_item(row_idx, 2, QTableWidgetItem(provider["kind"].replace("-", " ")))
            self._set_provider_item(row_idx, 3, QTableWidgetItem(provider.get("base_url") or "Built-in"))

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
        self.providers_table.blockSignals(False)
        self.providers_table.resizeRowsToContents()
        if self._providers:
            self.providers_table.selectRow(min(selected_row, len(self._providers) - 1))
        else:
            self._refresh_model_order_list(-1)

    def _set_provider_item(self, row: int, column: int, item: QTableWidgetItem):
        item.setFlags(
            item.flags()
            | Qt.ItemFlag.ItemIsDragEnabled
            | Qt.ItemFlag.ItemIsDropEnabled
        )
        self.providers_table.setItem(row, column, item)

    def _move_provider(self, source: int, dest: int):
        if source < 0 or source >= len(self._providers):
            return
        dest = max(0, min(dest, len(self._providers) - 1))
        if source == dest:
            return
        provider = self._providers.pop(source)
        self._providers.insert(dest, provider)
        self._refresh_provider_table()
        self.providers_table.selectRow(dest)

    def _on_provider_selection_changed(self):
        self._refresh_model_order_list(self.providers_table.currentRow())

    def _refresh_model_order_list(self, row: int):
        self._model_order_provider_row = row
        self.model_order_list.blockSignals(True)
        self.model_order_list.clear()
        if 0 <= row < len(self._providers):
            for model in self._providers[row].get("models", []):
                mid = model.get("id", "")
                name = model.get("name", mid)
                item = QListWidgetItem(name if name == mid else f"{name} ({mid})")
                item.setIcon(_drag_handle_icon())
                item.setData(Qt.ItemDataRole.UserRole, dict(model))
                item.setFlags(
                    item.flags()
                    | Qt.ItemFlag.ItemIsDragEnabled
                    | Qt.ItemFlag.ItemIsDropEnabled
                )
                self.model_order_list.addItem(item)
            self.model_order_list.setEnabled(True)
        else:
            self.model_order_list.setEnabled(False)
        self.model_order_list.blockSignals(False)

    def _apply_model_order(self):
        row = self._model_order_provider_row
        if row < 0 or row >= len(self._providers):
            return
        models = []
        for idx in range(self.model_order_list.count()):
            data = self.model_order_list.item(idx).data(Qt.ItemDataRole.UserRole)
            if isinstance(data, dict) and data.get("id"):
                models.append(dict(data))
        if not models:
            return
        provider = self._providers[row]
        provider["models"] = models

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
            self.providers_table.selectRow(len(self._providers) - 1)

    def _edit_provider(self, row: int):
        if row < 0 or row >= len(self._providers):
            return
        existing = {provider["id"] for provider in self._providers}
        dialog = _ProviderDialog(self._styles, existing, self._providers[row], self)
        if dialog.exec():
            self._providers[row] = dialog.value()
            self._refresh_provider_table()
            self.providers_table.selectRow(row)

    def _remove_provider(self, row: int):
        if row < 0 or row >= len(self._providers):
            return
        del self._providers[row]
        self._refresh_provider_table()

    def _page_crew(self, saved: dict) -> QWidget:
        page, layout = self._page_shell(
            "Crew",
            "Configure aichs and each optional crew member's voice, model, color, and portrait.",
        )
        self._crew_widgets = {}
        p = palette()
        tabs = QTabWidget()
        apply_flat_tab_style(tabs, "crewSettingsTabs")

        lead_tab = QWidget()
        lead_layout = QVBoxLayout(lead_tab)
        lead_layout.setContentsMargins(14, 14, 14, 14)
        lead_layout.setSpacing(10)

        lead_header = QHBoxLayout()
        lead_title = QLabel("aichs · Lead agent")
        lead_title.setStyleSheet(f"color:{p['TEXT']}; font-weight:bold;")
        lead_header.addWidget(lead_title)
        lead_header.addStretch()
        lead_status = QLabel("Always active")
        lead_status.setStyleSheet(
            f"color:{ACCENT}; background:{p['BG3']}; border:1px solid {p['BORDER']};"
            "border-radius:6px; padding:4px 8px; font-size:12px;"
        )
        lead_header.addWidget(lead_status)
        lead_layout.addLayout(lead_header)

        lead_desc = QLabel("The main agent that owns the conversation and invites crew members when useful.")
        lead_desc.setWordWrap(True)
        lead_desc.setStyleSheet(self._hint_style)
        lead_layout.addWidget(lead_desc)

        self.agent_portrait = _PortraitPicker(
            "agent", "aichs avatar", saved.get("avatar_agent", "agent"), self._styles,
        )
        lead_layout.addWidget(self.agent_portrait)

        prompt_row = QHBoxLayout()
        prompt_label = QLabel("Personality and instructions")
        prompt_label.setStyleSheet(f"color:{p['TEXT']}; font-weight:bold;")
        prompt_row.addWidget(prompt_label)
        prompt_row.addStretch()
        reset_btn = QPushButton("Reset to default")
        reset_btn.setStyleSheet(self._btn_style)
        reset_btn.clicked.connect(lambda: self.system_prompt.setPlainText(SYSTEM_PROMPT))
        prompt_row.addWidget(reset_btn)
        lead_layout.addLayout(prompt_row)

        self.system_prompt = QTextEdit()
        self.system_prompt.setStyleSheet(self._field_style)
        self.system_prompt.setMinimumHeight(180)
        lead_layout.addWidget(self.system_prompt, 1)
        lead_layout.addStretch()
        tabs.addTab(lead_tab, "aichs")

        for member in all_crew():
            cfg = crew_settings(saved, member)
            tab = QWidget()
            card_layout = QVBoxLayout(tab)
            card_layout.setContentsMargins(14, 14, 14, 14)
            card_layout.setSpacing(10)

            header = QHBoxLayout()
            enabled = QCheckBox(f"{member.name} · {member.title}")
            enabled.setChecked(cfg["enabled"])
            enabled.setStyleSheet(f"color:{palette()['TEXT']}; font-weight:bold;")
            header.addWidget(enabled)
            header.addStretch()
            card_layout.addLayout(header)

            desc = QLabel(member.description)
            desc.setWordWrap(True)
            desc.setStyleSheet(self._hint_style)
            card_layout.addWidget(desc)

            controls = QHBoxLayout()
            model = QComboBox()
            model.setStyleSheet(self._field_style)
            model.addItem("Use chat model", "")
            for provider, model_ids in MODELS.items():
                for model_id in model_ids:
                    model.addItem(f"{_provider_title(provider)} · {model_id}", model_id)
            idx = model.findData(cfg["model"])
            if idx >= 0:
                model.setCurrentIndex(idx)
            controls.addWidget(model, 1)

            fallback_color = crew_tone(member.id)["accent"]
            color = _ColorPicker(cfg["color"], fallback_color, self._styles)
            controls.addWidget(color)
            card_layout.addLayout(controls)

            portrait = _PortraitPicker(
                f"crew_{member.id}",
                f"{member.name} avatar",
                cfg["avatar"],
                self._styles,
                default_source=f"crew_{member.id}",
                accent_color=color.display_color(),
            )
            color.color_changed.connect(portrait.set_accent_color)
            card_layout.addWidget(portrait)

            prompt = QTextEdit()
            prompt.setStyleSheet(self._field_style)
            prompt.setMinimumHeight(86)
            prompt.setPlaceholderText(member.prompt)
            prompt.setPlainText(cfg["prompt"])
            card_layout.addWidget(prompt)

            self._crew_widgets[member.id] = {
                "enabled": enabled,
                "model": model,
                "color": color,
                "portrait": portrait,
                "prompt": prompt,
            }
            card_layout.addStretch()
            tabs.addTab(tab, member.name)
        layout.addWidget(self._section_separator())
        layout.addWidget(tabs, 1)
        layout.addStretch()
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

        self.enter_to_send_check.setChecked(bool(saved.get("enter_to_send", False)))

        compaction = saved.get("compaction") if isinstance(saved.get("compaction"), dict) else {}
        reserve = compaction.get("reserve_tokens", compaction.get("reserveTokens"))
        keep = compaction.get("keep_recent_tokens", compaction.get("keepRecentTokens"))
        self.compaction_reserve_edit.setText("" if reserve is None else str(int(reserve)))
        self.compaction_keep_recent_edit.setText("" if keep is None else str(int(keep)))

        self._update_compaction_label()

    def _update_compaction_label(self):
        if self._providers:
            examples = ", ".join(
                _provider_compaction_example(provider) for provider in self._providers
            )
        else:
            from services.compaction import compaction_threshold

            examples = (
                f"Claude ~{compaction_threshold(180_000):,}, "
                f"OpenAI-compatible ~{compaction_threshold(100_000):,}"
            )
        self.compaction_label.setText(
            "Compact when context exceeds the model window minus a scaled reserve "
            f"(~{examples} tokens for your models). Use /compact to summarize manually."
        )

    def _save(self):
        data = self.store.load()
        configured_ids = {provider["id"] for provider in self._providers}
        provider_keys = {
            provider["id"]: provider.get("api_key", "").strip()
            for provider in self._providers
        }
        existing_defaults = data.get("default_models", {})
        default_models = {}
        for provider in self._providers:
            provider_id = provider["id"]
            saved_default = str(existing_defaults.get(provider_id, ""))
            if saved_default and saved_default in _model_ids(provider.get("models", [])):
                default_models[provider_id] = saved_default
        crew = {}
        crew_models = {}
        for member in all_crew():
            widgets = self._crew_widgets.get(member.id, {})
            if not widgets:
                continue
            model = widgets["model"].currentData() or ""
            avatar = persist_portrait(
                widgets["portrait"].value(),
                f"crew_{member.id}",
            )
            crew[member.id] = {
                "enabled": widgets["enabled"].isChecked(),
                "model": model,
                "prompt": widgets["prompt"].toPlainText().strip(),
                "color": widgets["color"].value(),
                "avatar": avatar,
            }
            if model:
                crew_models[member.id] = model

        user_providers = {}
        for provider in self._providers:
            provider_id = provider["id"]
            is_builtin = provider_id in _BUILTIN_IDS
            has_model_override = _has_builtin_model_order_override(
                provider_id,
                provider.get("models", []),
            )
            has_override = bool(provider.get("base_url")) or not is_builtin or has_model_override
            if not has_override:
                continue
            entry = {
                "api": provider.get("api", "openai-compatible"),
                "apiKey": provider.get("api_key_spec") or _provider_env_var(provider_id),
            }
            if provider.get("base_url"):
                entry["baseUrl"] = provider["base_url"]
            if not is_builtin or has_model_override:
                models = []
                for model in provider.get("models", []):
                    model_entry = {"id": model["id"]}
                    if model.get("name") and model["name"] != model["id"]:
                        model_entry["name"] = model["name"]
                    if not is_builtin:
                        window = _model_context_window(model)
                        if window:
                            model_entry["contextWindow"] = window
                    models.append(model_entry)
                entry["models"] = models
            user_providers[provider_id] = entry

        from services.compaction import parse_compaction_token

        compaction: dict = {}
        reserve = parse_compaction_token(self.compaction_reserve_edit.text())
        keep = parse_compaction_token(self.compaction_keep_recent_edit.text())
        if reserve is not None:
            compaction["reserve_tokens"] = reserve
        if keep is not None:
            compaction["keep_recent_tokens"] = keep

        data.update({
            "anthropic_api_key": provider_keys.get("claude", ""),
            "openai_api_key": provider_keys.get("openai", ""),
            "provider_api_keys": provider_keys,
            "system_prompt": self.system_prompt.toPlainText().strip() or SYSTEM_PROMPT,
            "theme": self.theme_combo.currentText(),
            "font_size": self.font_combo.currentText(),
            "enter_to_send": self.enter_to_send_check.isChecked(),
            "default_models": default_models,
            "provider_order": [provider["id"] for provider in self._providers],
            "crew": crew,
            "crew_models": crew_models,
            "avatar_human": persist_portrait(self.human_portrait.value(), "human"),
            "avatar_agent": persist_portrait(self.agent_portrait.value(), "agent"),
        })
        if compaction:
            data["compaction"] = compaction
        else:
            data.pop("compaction", None)
        if not configured_ids:
            data["default_models"] = {}
            data["provider_api_keys"] = {}
            data["provider_order"] = []
            data["anthropic_api_key"] = ""
            data["openai_api_key"] = ""
        save_user_providers(user_providers)
        model_registry.reload()
        self.store.save(data)
        self.store.apply_saved(data)
        clear_cache()
        self.accept()
