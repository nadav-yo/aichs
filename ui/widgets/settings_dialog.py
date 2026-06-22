from pathlib import Path
import os
import re
import threading

import config
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTextEdit, QComboBox, QWidget, QFileDialog, QScrollArea,
    QListWidget, QListWidgetItem, QStackedWidget, QFrame, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QToolButton, QStyle, QCheckBox,
    QSpinBox, QDoubleSpinBox, QColorDialog, QTabWidget, QAbstractItemView,
    QSizePolicy, QDialogButtonBox, QTreeWidget, QTreeWidgetItem,
)
from PyQt6.QtCore import QObject, QRunnable, QThreadPool, Qt, QSize, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QIntValidator, QPainter, QPen, QPixmap

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
from services.yuk import (
    YukExportSelection,
    apply_yuk,
    discover_export_items,
    export_yuk,
    inspect_yuk,
)
from storage.settings import (
    ARCHIVIST_PROMPT_KEY,
    AUTO_TITLE_PROMPT_INSTRUCTIONS_KEY,
    COMPACT_RESUME_PROMPT_KEY,
    COMPACTION_SUMMARY_GUIDANCE_KEY,
    COMMIT_MESSAGE_PROMPT_ADDITION_KEY,
    CANVAS_ACTION_AUTO_APPROVE_KEY,
    CANVAS_PARALLEL_LIMIT_KEY,
    CANVAS_RUN_MODE_KEY,
    DEFAULT_ARCHIVIST_PROMPT,
    DEFAULT_AUTO_TITLE_PROMPT_INSTRUCTIONS,
    DEFAULT_CANVAS_ACTION_AUTO_APPROVE,
    DEFAULT_CANVAS_RUN_MODE,
    DEFAULT_COMPACT_RESUME_PROMPT,
    DEFAULT_DIAGNOSTIC_FIX_PROMPT_TEMPLATE,
    DEFAULT_FILE_EDITOR_TAB_SPACES,
    DEFAULT_FILE_REVIEW_PROMPT_TEMPLATE,
    DEFAULT_GRAPH_AGENT_PROMPT,
    DEFAULT_GRAPH_GENERATION_STRATEGY,
    DEFAULT_GIT_FIX_PROMPT_TEMPLATE,
    DEFAULT_TRASH_RETENTION_DAYS,
    DIAGNOSTIC_FIX_PROMPT_TEMPLATE_KEY,
    FILE_EDITOR_AUTO_SAVE_KEY,
    FILE_EDITOR_TAB_SPACES_KEY,
    FILE_REVIEW_PROMPT_TEMPLATE_KEY,
    GRAPH_AGENT_PROMPT_KEY,
    GRAPH_GENERATION_STRATEGY_KEY,
    GIT_FIX_PROMPT_TEMPLATE_KEY,
    MAX_CANVAS_PARALLEL_LIMIT,
    MAX_FILE_EDITOR_TAB_SPACES,
    MIN_CANVAS_PARALLEL_LIMIT,
    MIN_FILE_EDITOR_TAB_SPACES,
    TRASH_RETENTION_DAYS_KEY,
    SettingsStore,
    archivist_prompt,
    auto_title_prompt_instructions,
    canvas_action_auto_approve,
    canvas_parallel_limit,
    canvas_run_mode,
    compact_resume_prompt,
    compaction_summary_guidance,
    diagnostic_fix_prompt_template,
    file_editor_tab_spaces,
    resume_session,
    RESUME_SESSION_KEY,
    DEFAULT_RESUME_SESSION,
    file_review_prompt_template,
    git_fix_prompt_template,
    graph_agent_prompt,
    graph_generation_strategy,
    trash_retention_days,
)
from ui.avatars import avatar_pixmap, clear_cache, persist_portrait
from ui.theme import (
    palette, DEFAULT_FONT_SIZE, DEFAULT_THEME,
    avatar_preview_style,
    bordered_icon_button_style, checkbox_style,
    apply_flat_tab_style, compact_combo_box_style, contained_list_style,
    contained_tree_style, crew_tone, data_table_style,
    dialog_button_box_style, dialog_shell_style,
    navigation_list_style, panel_stack_style,
    separator_color, separator_frame_style, surface_frame_style,
    primary_button_style, form_field_style, field_label_style, hint_label_style, meta_font_pt, secondary_button_style,
    status_pill_style, title_label_style,
)

_NAV = [
    ("general", "General"),
    ("editor", "Editor"),
    ("canvas", "Canvas"),
    ("prompts", "Prompts"),
    ("models", "Models"),
    ("crew", "Crew"),
    ("user_kit", "User Kit"),
]

_BUILTIN_IDS = {"claude", "openai"}
_MODEL_CONTEXT_SUFFIX = re.compile(r"\s@\s*(\d+)\s*$")
_CUSTOM_DEFAULT_CONTEXT = 32_768
_PROVIDER_MODEL_EDITOR_HEIGHTS = {
    "anthropic": 112,
    "openai": 132,
    "custom": 88,
}
_TEMPERATURE_TIP = "Randomness. Lower is steadier; higher explores more. Default lets the provider decide."
_TOP_K_TIP = "Limits sampling to the top token candidates when supported. Default omits it."
_MIN_P_TIP = "Filters low-probability tokens relative to the best token when supported. Default omits it."


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


def _float_range(value, minimum: float, maximum: float) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if minimum <= parsed <= maximum else None


def _provider_temperature(source) -> float | None:
    return _float_range((source or {}).get("temperature"), 0.0, 2.0)


def _provider_top_k(source) -> int | None:
    raw = (source or {}).get("topK", (source or {}).get("top_k"))
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        return None
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= -1 else None


def _provider_min_p(source) -> float | None:
    return _float_range((source or {}).get("minP", (source or {}).get("min_p")), 0.0, 1.0)


def _generation_values(source) -> dict:
    values = {}
    temperature = _provider_temperature(source)
    top_k = _provider_top_k(source)
    min_p = _provider_min_p(source)
    if temperature is not None:
        values["temperature"] = temperature
    if top_k is not None:
        values["top_k"] = top_k
    if min_p is not None:
        values["min_p"] = min_p
    return values


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
        palette()

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)

        self.preview = QLabel()
        self.preview.setFixedSize(48, 48)
        self.preview.setStyleSheet(avatar_preview_style())

        col = QVBoxLayout()
        col.setSpacing(4)
        title = QLabel(label)
        title.setStyleSheet(title_label_style(font_pt=13, font_weight="bold"))
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
        self.preview.setStyleSheet(
            avatar_preview_style(border_color=border, border_width=2),
        )
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
        self.resize(480, 620)
        self.setMinimumHeight(620)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        palette()
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

        generation_row = QHBoxLayout()
        generation_row.setSpacing(8)
        self.temperature = QDoubleSpinBox()
        self.temperature.setRange(-1.0, 2.0)
        self.temperature.setSingleStep(0.05)
        self.temperature.setDecimals(2)
        self.temperature.setSpecialValueText("Default")
        self.temperature.setValue(-1.0)
        self.temperature.setStyleSheet(styles["field"])
        self._generation_field(generation_row, "Temperature", self.temperature, _TEMPERATURE_TIP)

        self.top_k = QLineEdit()
        self.top_k.setPlaceholderText("Default")
        self.top_k.setValidator(QIntValidator(-1, 100000, self.top_k))
        self.top_k.setStyleSheet(styles["field"])
        self._generation_field(generation_row, "Top K", self.top_k, _TOP_K_TIP)

        self.min_p = QDoubleSpinBox()
        self.min_p.setRange(-1.0, 1.0)
        self.min_p.setSingleStep(0.01)
        self.min_p.setDecimals(2)
        self.min_p.setSpecialValueText("Default")
        self.min_p.setValue(-1.0)
        self.min_p.setStyleSheet(styles["field"])
        self._generation_field(generation_row, "Min P", self.min_p, _MIN_P_TIP)
        root.addLayout(generation_row)

        self.models = QTextEdit()
        self.models.setPlaceholderText("model-id\nmodel-id = Display Name")
        self.models.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
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
        add.setStyleSheet(primary_button_style())
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
            self._set_optional_double(self.temperature, data.get("temperature"))
            self._set_optional_int_text(self.top_k, data.get("top_k"))
            self._set_optional_double(self.min_p, data.get("min_p"))
            self._apply_kind_ui(kind)
            self.models.setPlainText(
                _models_to_text(
                    data.get("models", []),
                    include_context=kind == "custom",
                ),
            )
        else:
            self._apply_kind_defaults()

        self.setStyleSheet(dialog_shell_style())

    def _apply_kind_ui(self, kind: str):
        self._set_models_height(kind)
        if kind == "custom":
            self.models.setPlaceholderText(
                "llama3.1:8b = Llama 3.1 8B @ 32768\n"
                "qwen2.5-coder:7b @ 65536"
            )
            self.hint.setText(
                "For custom providers (e.g. Ollama), append @ tokens to each model line "
                f"(defaults to {custom_default_context_window():,} when omitted). "
                f"Models are saved to {config.AICHS_HOME / 'models.json'}; API keys stay in settings."
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
                    f"Models are saved to {config.AICHS_HOME / 'models.json'}; API keys stay in settings."
                )

        if self.layout():
            self.layout().invalidate()
            self.layout().activate()

    def _set_models_height(self, kind: str):
        height = _PROVIDER_MODEL_EDITOR_HEIGHTS.get(kind, _PROVIDER_MODEL_EDITOR_HEIGHTS["custom"])
        self.models.setFixedHeight(height)
        self.models.updateGeometry()

    def _field(self, layout: QVBoxLayout, label: str, widget: QWidget):
        lbl = QLabel(label)
        lbl.setStyleSheet(self._styles["label"])
        layout.addWidget(lbl)
        layout.addWidget(widget)

    def _generation_field(self, layout: QHBoxLayout, label: str, widget: QWidget, tooltip: str):
        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel(label)
        lbl.setStyleSheet(self._styles["label"])
        lbl.setToolTip(tooltip)
        widget.setToolTip(tooltip)
        col.addWidget(lbl)
        col.addWidget(widget)
        layout.addLayout(col, 1)

    def _set_optional_double(self, spin: QDoubleSpinBox, value):
        parsed = _float_range(value, 0.0, spin.maximum())
        spin.setValue(parsed if parsed is not None else spin.minimum())

    def _set_optional_int_text(self, edit: QLineEdit, value):
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            edit.clear()
            return
        if parsed >= -1:
            edit.setText(str(parsed))
        else:
            edit.clear()

    def _optional_double(self, spin: QDoubleSpinBox) -> float | None:
        if spin.value() <= spin.minimum():
            return None
        return round(float(spin.value()), 4)

    def _optional_int_text(self, edit: QLineEdit) -> int | None:
        text = edit.text().strip()
        if not text:
            return None
        try:
            value = int(text)
        except ValueError:
            return None
        return value if value >= -1 else None

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
        value = {
            "id": provider_id,
            "kind": kind,
            "api": api,
            "base_url": self.base_url.text().strip(),
            "api_key": self.api_key.text().strip(),
            "api_key_spec": api_key_spec,
            "models": _parse_models(self.models.toPlainText()),
        }
        temperature = self._optional_double(self.temperature)
        top_k = self._optional_int_text(self.top_k)
        min_p = self._optional_double(self.min_p)
        if temperature is not None:
            value["temperature"] = temperature
        if top_k is not None:
            value["top_k"] = top_k
        if min_p is not None:
            value["min_p"] = min_p
        return value


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


def _hint_label(text: str, style: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(style)
    return label


class _YukExportItemsSignals(QObject):
    done = pyqtSignal(int, object, str)


class _YukExportItemsWorker(QRunnable):
    def __init__(self, generation: int, cwd: str):
        super().__init__()
        self.signals = _YukExportItemsSignals()
        self._generation = generation
        self._cwd = cwd

    def run(self) -> None:
        try:
            items = discover_export_items(self._cwd)
        except Exception as exc:
            self.signals.done.emit(self._generation, [], str(exc))
            return
        self.signals.done.emit(self._generation, items, "")


class _YukExportPackageSignals(QObject):
    done = pyqtSignal(int, object, str)


class _YukExportPackageWorker(QRunnable):
    def __init__(self, generation: int, path: str, cwd: str, selected_item_ids: set[str]):
        super().__init__()
        self.signals = _YukExportPackageSignals()
        self._generation = generation
        self._path = path
        self._cwd = cwd
        self._selected_item_ids = set(selected_item_ids)
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            manifest = export_yuk(
                self._path,
                self._cwd,
                YukExportSelection(selected_item_ids=self._selected_item_ids),
                cancelled=self._cancel.is_set,
            )
        except Exception as exc:
            self.signals.done.emit(self._generation, None, str(exc))
            return
        self.signals.done.emit(self._generation, manifest, "")


class _YukInspectSignals(QObject):
    done = pyqtSignal(int, str, object, str)


class _YukInspectWorker(QRunnable):
    def __init__(self, generation: int, path: str, cwd: str):
        super().__init__()
        self.signals = _YukInspectSignals()
        self._generation = generation
        self._path = path
        self._cwd = cwd

    def run(self) -> None:
        try:
            inspection = inspect_yuk(self._path, self._cwd)
        except Exception as exc:
            self.signals.done.emit(self._generation, self._path, None, str(exc))
            return
        self.signals.done.emit(self._generation, self._path, inspection, "")


class _YukApplySignals(QObject):
    done = pyqtSignal(int, object, str)


class _YukApplyWorker(QRunnable):
    def __init__(self, generation: int, path: str, cwd: str, choices: dict[str, str]):
        super().__init__()
        self.signals = _YukApplySignals()
        self._generation = generation
        self._path = path
        self._cwd = cwd
        self._choices = dict(choices)

    def run(self) -> None:
        try:
            result = apply_yuk(self._path, self._cwd, self._choices)
        except Exception as exc:
            self.signals.done.emit(self._generation, None, str(exc))
            return
        self.signals.done.emit(self._generation, result, "")


class _YukExportDialog(QDialog):
    def __init__(self, cwd: str, parent=None):
        super().__init__(parent)
        self._cwd = cwd
        self._syncing = False
        self._items_generation = 0
        self._items_loaded = False
        self._items_pool = QThreadPool.globalInstance()
        self.setWindowTitle("Export YUK")
        self.resize(720, 560)

        p = palette()
        self.setStyleSheet(
            f"QDialog {{ background:{p['BG2']}; color:{p['TEXT']}; }}"
            f"{contained_tree_style(item_padding='5px', border_radius=6, bg=p['BG3'], border=p['BORDER'])}"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        title = QLabel("Export User Kit")
        title.setStyleSheet(title_label_style())
        root.addWidget(title)
        root.addWidget(_hint_label(
            "Choose the personalization pieces to package. Models, API keys, conversations, and runtime state are never exported.",
            hint_label_style(),
        ))

        controls = QHBoxLayout()
        select_all = QPushButton("Select all")
        deselect_all = QPushButton("Deselect all")
        select_all.clicked.connect(lambda: self._set_all(Qt.CheckState.Checked))
        deselect_all.clicked.connect(lambda: self._set_all(Qt.CheckState.Unchecked))
        controls.addWidget(select_all)
        controls.addWidget(deselect_all)
        controls.addStretch()
        root.addLayout(controls)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Item", "Scope", "Details"])
        self.tree.setColumnWidth(0, 300)
        self.tree.setColumnWidth(1, 90)
        self.tree.itemChanged.connect(self._on_item_changed)
        root.addWidget(self.tree, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.setStyleSheet(dialog_button_box_style())
        self._export_btn = buttons.addButton("Export", QDialogButtonBox.ButtonRole.AcceptRole)
        self._export_btn.setEnabled(False)
        self._export_btn.clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._start_item_load()

    def selected_item_ids(self) -> set[str]:
        selected: set[str] = set()
        for row in range(self.tree.topLevelItemCount()):
            section = self.tree.topLevelItem(row)
            for idx in range(section.childCount()):
                child = section.child(idx)
                if child.checkState(0) == Qt.CheckState.Checked:
                    selected.add(str(child.data(0, Qt.ItemDataRole.UserRole) or ""))
        return {item_id for item_id in selected if item_id}

    def accept(self) -> None:
        if not self._items_loaded:
            return
        super().accept()

    def reject(self) -> None:
        self._items_generation += 1
        super().reject()

    def closeEvent(self, event) -> None:
        self._items_generation += 1
        super().closeEvent(event)

    def _start_item_load(self) -> None:
        self._items_generation += 1
        generation = self._items_generation
        self._items_loaded = False
        self._export_btn.setEnabled(False)
        self._show_loading()
        worker = _YukExportItemsWorker(generation, self._cwd)
        worker.signals.done.connect(self._on_items_ready)
        self._items_pool.start(worker)

    def _on_items_ready(self, generation: int, items: object, error: str) -> None:
        if generation != self._items_generation:
            return
        if error:
            self.tree.clear()
            self.tree.addTopLevelItem(QTreeWidgetItem([f"Could not load export items: {error}", "", ""]))
            return
        self._items_loaded = True
        self._populate(list(items or []))
        self._export_btn.setEnabled(True)

    def _show_loading(self) -> None:
        self.tree.clear()
        self.tree.addTopLevelItem(QTreeWidgetItem(["Loading export items...", "", ""]))

    def _populate(self, items: list) -> None:
        self.tree.clear()
        by_section: dict[str, list] = {}
        for item in items:
            by_section.setdefault(item.section, []).append(item)
        for section_name, items in by_section.items():
            section = QTreeWidgetItem([section_name, "", f"{len(items)} item(s)"])
            section.setFlags(section.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate)
            section.setCheckState(0, Qt.CheckState.Checked)
            self.tree.addTopLevelItem(section)
            section.setExpanded(True)
            for item in items:
                detail = item.note or item.kind.replace("_", " ")
                child = QTreeWidgetItem([item.label, item.scope.title(), detail])
                child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                child.setCheckState(0, Qt.CheckState.Checked if item.selected else Qt.CheckState.Unchecked)
                child.setData(0, Qt.ItemDataRole.UserRole, item.id)
                section.addChild(child)
        self.tree.expandAll()

    def _set_all(self, state: Qt.CheckState) -> None:
        self._syncing = True
        for row in range(self.tree.topLevelItemCount()):
            section = self.tree.topLevelItem(row)
            section.setCheckState(0, state)
            for idx in range(section.childCount()):
                section.child(idx).setCheckState(0, state)
        self._syncing = False

    def _on_item_changed(self, item: QTreeWidgetItem, _column: int) -> None:
        if self._syncing:
            return
        self._syncing = True
        if item.parent() is None:
            state = item.checkState(0)
            if state in (Qt.CheckState.Checked, Qt.CheckState.Unchecked):
                for idx in range(item.childCount()):
                    item.child(idx).setCheckState(0, state)
        else:
            parent = item.parent()
            checked = sum(
                1 for idx in range(parent.childCount())
                if parent.child(idx).checkState(0) == Qt.CheckState.Checked
            )
            if checked == 0:
                parent.setCheckState(0, Qt.CheckState.Unchecked)
            elif checked == parent.childCount():
                parent.setCheckState(0, Qt.CheckState.Checked)
            else:
                parent.setCheckState(0, Qt.CheckState.PartiallyChecked)
        self._syncing = False


class _YukImportDialog(QDialog):
    def __init__(self, inspection, parent=None):
        super().__init__(parent)
        self._inspection = inspection
        self._conflicts = {conflict.item_id: conflict for conflict in inspection.conflicts}
        self._action_widgets: dict[str, QComboBox] = {}
        self.setWindowTitle("Import YUK")
        self.resize(760, 560)

        p = palette()
        self.setStyleSheet(
            f"QDialog {{ background:{p['BG2']}; color:{p['TEXT']}; }}"
            f"{contained_tree_style(item_padding='5px', border_radius=6, bg=p['BG3'], border=p['BORDER'])}"
            f"{compact_combo_box_style(background=p['BG2'], popup_background=p['BG3'], font_pt=13, padding='3px 8px', border_radius=5)}"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        title = QLabel("Import User Kit")
        title.setStyleSheet(title_label_style())
        root.addWidget(title)
        package_name = str(inspection.manifest.get("name") or "YUK package")
        root.addWidget(_hint_label(
            f"{package_name}. Review the contents before applying. Extension Python is not executed during preview. Imported extensions are installed disabled until reviewed.",
            hint_label_style(),
        ))
        if inspection.warnings:
            warning = QLabel("Warnings:\n" + "\n".join(f"- {text}" for text in inspection.warnings))
            warning.setWordWrap(True)
            warning.setStyleSheet(
                "color:#f59e0b; background:rgba(245, 158, 11, 0.10);"
                "border:1px solid rgba(245, 158, 11, 0.35); border-radius:6px;"
                f"padding:8px; font-size:{meta_font_pt()}px;"
            )
            root.addWidget(warning)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Item", "Scope", "Action"])
        self.tree.setColumnWidth(0, 330)
        self.tree.setColumnWidth(1, 90)
        root.addWidget(self.tree, 1)
        self._populate()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.setStyleSheet(dialog_button_box_style())
        import_btn = buttons.addButton("Import", QDialogButtonBox.ButtonRole.AcceptRole)
        import_btn.clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def choices(self) -> dict[str, str]:
        choices: dict[str, str] = {}
        for row in range(self.tree.topLevelItemCount()):
            section = self.tree.topLevelItem(row)
            for idx in range(section.childCount()):
                child = section.child(idx)
                item_id = str(child.data(0, Qt.ItemDataRole.UserRole) or "")
                if not item_id:
                    continue
                if child.checkState(0) != Qt.CheckState.Checked:
                    choices[item_id] = "skip"
                    continue
                combo = self._action_widgets.get(item_id)
                if combo:
                    choices[item_id] = str(combo.currentData())
        return choices

    def _populate(self) -> None:
        by_section: dict[str, list] = {}
        seen_ids: set[str] = set()
        for item in self._inspection.items:
            by_section.setdefault(item.section or "Items", []).append(item)
            seen_ids.add(str(item.id))
        for key in sorted((self._inspection.manifest.get("settings") or {}).keys()):
            item_id = f"setting:{key}"
            if item_id in seen_ids:
                continue
            by_section.setdefault(_setting_section(key), []).append(
                type("SettingItem", (), {
                    "id": item_id,
                    "label": _setting_label_for_yuk(key),
                    "scope": "",
                    "note": "setting",
                    "section": _setting_section(key),
                })()
            )
        for section_name, items in by_section.items():
            section = QTreeWidgetItem([section_name, "", f"{len(items)} item(s)"])
            self.tree.addTopLevelItem(section)
            section.setExpanded(True)
            for item in items:
                item_id = str(item.id)
                conflict = self._conflicts.get(item_id)
                action_text = conflict.reason if conflict else _yuk_import_detail(item, self._inspection.manifest)
                child = QTreeWidgetItem([item.label, str(getattr(item, "scope", "")).title(), action_text])
                child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                child.setCheckState(0, Qt.CheckState.Checked)
                child.setData(0, Qt.ItemDataRole.UserRole, item_id)
                section.addChild(child)
                if conflict:
                    combo = QComboBox()
                    combo.addItem("Overwrite", "overwrite")
                    combo.addItem("Skip", "skip")
                    combo.addItem("Rename", "rename")
                    if conflict.kind == "setting":
                        combo.removeItem(2)
                    self.tree.setItemWidget(child, 2, combo)
                    self._action_widgets[item_id] = combo
        self.tree.expandAll()


def _setting_section(key: str) -> str:
    return "Crew" if key in {"crew", "crew_models", "avatar_human", "avatar_agent"} else "Personality & Prompts"


def _yuk_import_detail(item, manifest: dict) -> str:
    kind = str(getattr(item, "kind", "") or "")
    if kind not in {"extension_file", "extension_folder"}:
        return "Import"
    entry = _yuk_manifest_entry(manifest, str(getattr(item, "id", "") or ""))
    declared = bool(entry.get("permissions_declared"))
    permissions = entry.get("permissions") if isinstance(entry.get("permissions"), dict) else {}
    if not declared:
        return "Install disabled; permissions undisclosed"
    enabled = [key for key, value in permissions.items() if bool(value)]
    detail = ", ".join(enabled) if enabled else "none"
    return f"Install disabled; permissions: {detail}"


def _yuk_manifest_entry(manifest: dict, item_id: str) -> dict:
    for entry in manifest.get("items", []):
        if isinstance(entry, dict) and str(entry.get("id") or "") == item_id:
            return entry
    return {}


def _setting_label_for_yuk(key: str) -> str:
    return key.replace("_", " ").title()


def _normalized_crew_settings(saved: dict) -> dict:
    return {
        member.id: {
            "enabled": crew_settings(saved, member)["enabled"],
            "model": crew_settings(saved, member)["model"],
            "prompt": crew_settings(saved, member)["prompt"],
            "color": crew_settings(saved, member)["color"],
            "avatar": crew_settings(saved, member)["avatar"],
        }
        for member in all_crew()
    }


class SettingsDialog(QDialog):
    def __init__(self, store: SettingsStore, parent=None, cwd: str = ""):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("Settings")
        self.resize(640, 480)
        self.setMinimumSize(560, 420)

        p = palette()
        self._field_style = form_field_style()
        self._label_style = field_label_style()
        self._hint_style = hint_label_style()
        self._btn_style = secondary_button_style(
            border_radius=6,
            padding="4px 12px",
            font_size=12,
            text_color=p["TEXT_DIM"],
        )
        self._icon_btn_style = bordered_icon_button_style()
        self._checkbox_style = checkbox_style(
            font_pt=13,
            indicator_px=16,
            spacing_px=8,
        )
        self._strong_checkbox_style = checkbox_style(
            font_pt=13,
            font_weight="700",
            indicator_px=16,
            spacing_px=8,
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
        self._saved = saved
        self._cwd = cwd or str(saved.get("workspace_path") or os.getcwd())
        self._page_ids = [page_id for page_id, _title in _NAV]
        self._built_pages: set[str] = set()
        self._yuk_export_generation = 0
        self._yuk_export_active = False
        self._yuk_export_pool = QThreadPool(self)
        self._yuk_export_pool.setMaxThreadCount(1)
        self._yuk_export_worker: _YukExportPackageWorker | None = None
        self._yuk_import_generation = 0
        self._yuk_import_active = False
        self._yuk_import_pool = QThreadPool(self)
        self._yuk_import_pool.setMaxThreadCount(1)
        self._yuk_export_btn = None
        self._yuk_import_btn = None
        self._yuk_export_status = None

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
            navigation_list_style(
                border=f"0px solid {separator_color()}; border-right:1px solid {separator_color()}",
            )
        )
        for _id, title in _NAV:
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, _id)
            item.setSizeHint(QSize(0, 36))
            self._nav.addItem(item)
        self._nav.currentRowChanged.connect(self._on_nav)

        # ── pages ─────────────────────────────────────────────────────────
        self._stack = QStackedWidget()
        self._stack.setStyleSheet(panel_stack_style())

        for _page_id, title in _NAV:
            self._stack.addWidget(self._placeholder_page(title))

        body.addWidget(self._nav)
        body.addWidget(self._stack, 1)
        outer.addLayout(body, 1)

        # ── footer ────────────────────────────────────────────────────────
        footer = QFrame()
        footer.setStyleSheet(
            f"QFrame {{ background:{p['BG2']}; border:none;"
            f"border-top:1px solid {separator_color()}; }}"
        )
        buttons = QHBoxLayout(footer)
        buttons.setContentsMargins(16, 10, 16, 10)

        buttons.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save")
        save.setStyleSheet(primary_button_style(padding="6px 20px"))
        save.clicked.connect(self._save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        outer.addWidget(footer)

        self._ensure_page(0)
        self._nav.setCurrentRow(0)

    # ── page builders ─────────────────────────────────────────────────────

    def _placeholder_page(self, title: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 24)
        label = QLabel(title)
        label.setStyleSheet(self._hint_style)
        layout.addWidget(label)
        layout.addStretch()
        return page

    def _build_page(self, page_id: str) -> QWidget:
        if page_id == "general":
            return self._page_general(self._saved)
        if page_id == "editor":
            return self._page_editor(self._saved)
        if page_id == "canvas":
            return self._page_canvas(self._saved)
        if page_id == "prompts":
            return self._page_prompts(self._saved)
        if page_id == "models":
            return self._page_models()
        if page_id == "crew":
            return self._page_crew(self._saved)
        if page_id == "user_kit":
            return self._page_user_kit()
        raise ValueError(f"Unknown settings page: {page_id}")

    def _ensure_page(self, row: int) -> None:
        if row < 0 or row >= len(self._page_ids):
            return
        page_id = self._page_ids[row]
        if page_id in self._built_pages:
            return
        old = self._stack.widget(row)
        page = _scroll_page(self._build_page(page_id))
        self._stack.removeWidget(old)
        old.deleteLater()
        self._stack.insertWidget(row, page)
        self._built_pages.add(page_id)
        self._load_page_values(page_id, self._saved)

    def _ensure_all_pages(self) -> None:
        current = self._stack.currentIndex()
        for row in range(len(self._page_ids)):
            self._ensure_page(row)
        if current >= 0:
            self._stack.setCurrentIndex(current)

    def _page_shell(self, title: str, subtitle: str = "") -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 24)
        layout.setSpacing(16)
        palette()
        hdr = QLabel(title)
        hdr.setStyleSheet(title_label_style(font_pt=18, font_weight="700"))
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

    @staticmethod
    def _load_prompt_override(saved: dict, key: str, default: str = "") -> str:
        raw = str(saved.get(key, "") or "").strip()
        if not raw:
            return ""
        if default and raw == default.strip():
            return ""
        return raw

    def _prompt_field(self, layout: QVBoxLayout, label: str, widget: QWidget):
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel(label)
        lbl.setStyleSheet(self._label_style)
        row.addWidget(lbl)
        row.addStretch()
        reset = QPushButton("Reset")
        reset.setObjectName(
            "promptReset_" + re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
        )
        reset.setStyleSheet(self._btn_style)
        reset.setFixedHeight(28)
        reset.setToolTip("Clear custom text and use the built-in default")
        reset.setCursor(Qt.CursorShape.PointingHandCursor)

        def _reset():
            if isinstance(widget, QLineEdit):
                widget.clear()
            elif isinstance(widget, QTextEdit):
                widget.clear()

        reset.clicked.connect(_reset)
        row.addWidget(reset)
        layout.addLayout(row)
        layout.addWidget(widget)

    def _section_separator(self) -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(separator_frame_style())
        return sep

    def _page_general(self, saved: dict) -> QWidget:
        page, layout = self._page_shell(
            "General",
            "Look, feel, and composer behavior.",
        )
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["dark", "modern", "light"])
        self.theme_combo.setStyleSheet(self._field_style)
        self._field(layout, "Theme", self.theme_combo)

        self.font_combo = QComboBox()
        self.font_combo.addItems(["small", "medium", "large"])
        self.font_combo.setStyleSheet(self._field_style)
        self._field(layout, "Chat font size", self.font_combo)

        layout.addWidget(self._section_separator())

        self.enter_to_send_check = QCheckBox("Enter sends message")
        self.enter_to_send_check.setStyleSheet(self._checkbox_style)
        layout.addWidget(self.enter_to_send_check)

        self.resume_session_combo = QComboBox()
        self.resume_session_combo.addItem("Always", "always")
        self.resume_session_combo.addItem("Ask each time", "ask")
        self.resume_session_combo.addItem("Never", "never")
        self.resume_session_combo.setStyleSheet(self._field_style)
        self._field(layout, "Resume last session", self.resume_session_combo)

        resume_hint = QLabel(
            "When reopening a workspace, restore the last open chat, files, and editor layout."
        )
        resume_hint.setWordWrap(True)
        resume_hint.setStyleSheet(self._hint_style)
        layout.addWidget(resume_hint)

        enter_hint = QLabel("When enabled, Shift+Enter inserts a new line.")
        enter_hint.setWordWrap(True)
        enter_hint.setStyleSheet(self._hint_style)
        layout.addWidget(enter_hint)

        self.trash_retention_spin = QSpinBox()
        self.trash_retention_spin.setRange(1, 3650)
        self.trash_retention_spin.setSuffix(" days")
        self.trash_retention_spin.setStyleSheet(self._field_style)
        self._field(layout, "Clear deleted chats after", self.trash_retention_spin)

        trash_hint = QLabel(
            f"Deleted chats move to Trash and are permanently removed after this many days. Default is {DEFAULT_TRASH_RETENTION_DAYS}."
        )
        trash_hint.setWordWrap(True)
        trash_hint.setStyleSheet(self._hint_style)
        layout.addWidget(trash_hint)

        layout.addWidget(self._section_separator())
        self.human_portrait = _PortraitPicker(
            "human", "Your avatar", saved.get("avatar_human", "human"), self._styles,
        )
        layout.addWidget(self.human_portrait)
        layout.addStretch()
        return page

    def _page_editor(self, saved: dict) -> QWidget:
        page, layout = self._page_shell(
            "Editor",
            "File editor behavior.",
        )

        self.file_editor_auto_save_check = QCheckBox("Auto-save file editor changes")
        self.file_editor_auto_save_check.setStyleSheet(self._checkbox_style)
        layout.addWidget(self.file_editor_auto_save_check)

        auto_save_hint = QLabel(
            "When disabled, edited files are marked in the tab bar until you save or revert."
        )
        auto_save_hint.setWordWrap(True)
        auto_save_hint.setStyleSheet(self._hint_style)
        layout.addWidget(auto_save_hint)

        layout.addWidget(self._section_separator())
        self.file_editor_tab_spaces_spin = QSpinBox()
        self.file_editor_tab_spaces_spin.setRange(
            MIN_FILE_EDITOR_TAB_SPACES,
            MAX_FILE_EDITOR_TAB_SPACES,
        )
        self.file_editor_tab_spaces_spin.setStyleSheet(self._field_style)
        self._field(layout, "Tab width (spaces)", self.file_editor_tab_spaces_spin)

        tab_hint = QLabel(
            f"Controls visual tab width and Shift+Tab outdent size. Default is {DEFAULT_FILE_EDITOR_TAB_SPACES}."
        )
        tab_hint.setWordWrap(True)
        tab_hint.setStyleSheet(self._hint_style)
        layout.addWidget(tab_hint)

        layout.addStretch()
        return page

    def _page_canvas(self, saved: dict) -> QWidget:
        page, layout = self._page_shell(
            "Canvas",
            "Intent graph generation and run behavior.",
        )

        self.canvas_generation_strategy_combo = QComboBox()
        self.canvas_generation_strategy_combo.addItem("Prefer parallelism", "parallelism")
        self.canvas_generation_strategy_combo.addItem("Prefer atomicity", "atomicity")
        self.canvas_generation_strategy_combo.setStyleSheet(self._field_style)
        self._field(layout, "Step generation strategy", self.canvas_generation_strategy_combo)

        strategy_hint = QLabel(
            "Controls how Generate Steps shapes the graph. This does not force parallel execution."
        )
        strategy_hint.setWordWrap(True)
        strategy_hint.setStyleSheet(self._hint_style)
        layout.addWidget(strategy_hint)

        layout.addWidget(self._section_separator())

        self.canvas_run_mode_combo = QComboBox()
        self.canvas_run_mode_combo.addItem("Sequential", "sequential")
        self.canvas_run_mode_combo.addItem("Parallel", "parallel")
        self.canvas_run_mode_combo.setStyleSheet(self._field_style)
        self.canvas_run_mode_combo.currentIndexChanged.connect(self._sync_canvas_parallel_limit_enabled)
        self._field(layout, "Run mode", self.canvas_run_mode_combo)

        self.canvas_parallel_limit_spin = QSpinBox()
        self.canvas_parallel_limit_spin.setRange(MIN_CANVAS_PARALLEL_LIMIT, MAX_CANVAS_PARALLEL_LIMIT)
        self.canvas_parallel_limit_spin.setStyleSheet(self._field_style)
        self._field(layout, "Max parallel actions", self.canvas_parallel_limit_spin)

        self.canvas_action_auto_approve_combo = QComboBox()
        self.canvas_action_auto_approve_combo.addItem("Never", "never")
        self.canvas_action_auto_approve_combo.addItem("Coding actions only", "coder")
        self.canvas_action_auto_approve_combo.addItem("All actions", "all")
        self.canvas_action_auto_approve_combo.setStyleSheet(self._field_style)
        self._field(layout, "Auto-approve action results", self.canvas_action_auto_approve_combo)

        run_hint = QLabel(
            "Sequential runs one ready action at a time. Parallel starts independent ready actions up to the limit. Auto-approval applies only to action nodes; DoD review always waits for human approval."
        )
        run_hint.setWordWrap(True)
        run_hint.setStyleSheet(self._hint_style)
        layout.addWidget(run_hint)

        layout.addStretch()
        self._sync_canvas_parallel_limit_enabled()
        return page

    def _page_prompts(self, saved: dict) -> QWidget:
        page, layout = self._page_shell(
            "Prompts",
            "Workflow prompt defaults. Clear a field or use Reset to restore the built-in default; placeholders show what empty means.",
        )
        tabs = QTabWidget()
        apply_flat_tab_style(tabs, "promptSettingsTabs")

        drafts = QWidget()
        drafts_layout = QVBoxLayout(drafts)
        drafts_layout.setContentsMargins(14, 14, 14, 14)
        drafts_layout.setSpacing(10)

        self.file_review_prompt_template = QLineEdit()
        self.file_review_prompt_template.setPlaceholderText(
            DEFAULT_FILE_REVIEW_PROMPT_TEMPLATE
        )
        self.file_review_prompt_template.setStyleSheet(self._field_style)
        self._prompt_field(drafts_layout, "Ask File first line", self.file_review_prompt_template)
        drafts_layout.addWidget(_hint_label(
            "Replaces the first line of Ask File drafts. Use {mention} for a safe @ file reference or {path} for the raw relative path.",
            self._hint_style,
        ))

        self.diagnostic_fix_prompt_template = QLineEdit()
        self.diagnostic_fix_prompt_template.setPlaceholderText(
            DEFAULT_DIAGNOSTIC_FIX_PROMPT_TEMPLATE
        )
        self.diagnostic_fix_prompt_template.setStyleSheet(self._field_style)
        self._prompt_field(
            drafts_layout,
            "Diagnostic fix first line",
            self.diagnostic_fix_prompt_template,
        )
        drafts_layout.addWidget(_hint_label(
            "Replaces only the first line. Use {mention}, {path}, or {line}; diagnostic details are appended automatically.",
            self._hint_style,
        ))

        self.git_fix_prompt_template = QLineEdit()
        self.git_fix_prompt_template.setPlaceholderText(DEFAULT_GIT_FIX_PROMPT_TEMPLATE)
        self.git_fix_prompt_template.setStyleSheet(self._field_style)
        self._prompt_field(
            drafts_layout,
            "Git fix first line",
            self.git_fix_prompt_template,
        )
        drafts_layout.addWidget(_hint_label(
            "Replaces only the first line. Use {action}, {label}, {repo}, {command}, {exit_code}, or {output}; git details are appended automatically.",
            self._hint_style,
        ))

        self.compact_resume_prompt = QLineEdit()
        self.compact_resume_prompt.setPlaceholderText(DEFAULT_COMPACT_RESUME_PROMPT)
        self.compact_resume_prompt.setStyleSheet(self._field_style)
        self._prompt_field(drafts_layout, "Compact resume prompt", self.compact_resume_prompt)
        drafts_layout.addWidget(_hint_label(
            "Default resume message used when compact-and-resume does not provide its own text.",
            self._hint_style,
        ))
        drafts_layout.addStretch()
        tabs.addTab(drafts, "Drafts")

        automation = QWidget()
        automation_layout = QVBoxLayout(automation)
        automation_layout.setContentsMargins(14, 14, 14, 14)
        automation_layout.setSpacing(10)

        self.auto_title_prompt_instructions = QTextEdit()
        self.auto_title_prompt_instructions.setPlaceholderText(
            DEFAULT_AUTO_TITLE_PROMPT_INSTRUCTIONS
        )
        self.auto_title_prompt_instructions.setMinimumHeight(104)
        self.auto_title_prompt_instructions.setStyleSheet(self._field_style)
        self._prompt_field(
            automation_layout,
            "Auto-title instructions",
            self.auto_title_prompt_instructions,
        )
        automation_layout.addWidget(_hint_label(
            "Replaces the title-writing instructions. The first user message is attached automatically.",
            self._hint_style,
        ))
        automation_layout.addStretch()
        tabs.addTab(automation, "Titles")

        graph = QWidget()
        graph_layout = QVBoxLayout(graph)
        graph_layout.setContentsMargins(14, 14, 14, 14)
        graph_layout.setSpacing(10)

        self.graph_agent_prompt = QTextEdit()
        self.graph_agent_prompt.setPlaceholderText(DEFAULT_GRAPH_AGENT_PROMPT)
        self.graph_agent_prompt.setMinimumHeight(150)
        self.graph_agent_prompt.setStyleSheet(self._field_style)
        self._prompt_field(graph_layout, "Intent Graph agent prompt", self.graph_agent_prompt)
        graph_layout.addWidget(_hint_label(
            "Controls the inline graph agent. Graph tools and cycle validation stay fixed.",
            self._hint_style,
        ))
        graph_layout.addStretch()
        tabs.addTab(graph, "Graph")

        memory = QWidget()
        memory_layout = QVBoxLayout(memory)
        memory_layout.setContentsMargins(14, 14, 14, 14)
        memory_layout.setSpacing(10)

        self.compaction_summary_guidance = QTextEdit()
        self.compaction_summary_guidance.setPlaceholderText(
            "Optional. Example: Prefer terse bullets and preserve test commands exactly."
        )
        self.compaction_summary_guidance.setMaximumHeight(96)
        self.compaction_summary_guidance.setStyleSheet(self._field_style)
        self._prompt_field(memory_layout, "Extra compaction guidance", self.compaction_summary_guidance)
        memory_layout.addWidget(_hint_label(
            "Optional additive guidance appended to the fixed summary prompt; leave blank for the built-in behavior.",
            self._hint_style,
        ))

        self.archivist_prompt = QTextEdit()
        self.archivist_prompt.setPlaceholderText(DEFAULT_ARCHIVIST_PROMPT)
        self.archivist_prompt.setMinimumHeight(120)
        self.archivist_prompt.setStyleSheet(self._field_style)
        self._prompt_field(memory_layout, "Archivist slash-command prompt", self.archivist_prompt)
        memory_layout.addWidget(_hint_label(
            "Replaces /archivist instructions. Command name and allowed tools stay fixed.",
            self._hint_style,
        ))
        memory_layout.addStretch()
        tabs.addTab(memory, "Memory")

        git = QWidget()
        git_layout = QVBoxLayout(git)
        git_layout.setContentsMargins(14, 14, 14, 14)
        git_layout.setSpacing(10)

        self.commit_message_guidance = QTextEdit()
        self.commit_message_guidance.setPlaceholderText(
            "Optional. Example: Keep messages short. Use Jira issue keys when obvious."
        )
        self.commit_message_guidance.setMaximumHeight(96)
        self.commit_message_guidance.setStyleSheet(self._field_style)
        self._prompt_field(git_layout, "Extra commit guidance", self.commit_message_guidance)
        git_layout.addWidget(_hint_label(
            "Optional additive guidance appended to generated commit-message requests; leave blank for the built-in behavior.",
            self._hint_style,
        ))
        git_layout.addStretch()
        tabs.addTab(git, "Git")

        layout.addWidget(self._section_separator())
        layout.addWidget(tabs, 1)
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
        self.providers_table.setStyleSheet(data_table_style(border_radius=8))
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
            contained_list_style(
                item_padding="8px 10px",
                item_radius=6,
                border_radius=8,
                border=palette()["BORDER"],
            )
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
        generation = _generation_values(raw or {})
        if not raw and cfg:
            generation = _generation_values({
                "temperature": getattr(cfg, "temperature", None),
                "top_k": getattr(cfg, "top_k", None),
                "min_p": getattr(cfg, "min_p", None),
            })
        return {
            "id": provider_id,
            "kind": kind,
            "api": api,
            "base_url": (raw or {}).get("baseUrl", (raw or {}).get("base_url", cfg.base_url if cfg else "")) or "",
            "api_key": self._saved_provider_key(saved, provider_id),
            "api_key_spec": api_key_spec,
            "models": models,
            **generation,
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
        palette()
        tabs = QTabWidget()
        apply_flat_tab_style(tabs, "crewSettingsTabs")

        lead_tab = QWidget()
        lead_layout = QVBoxLayout(lead_tab)
        lead_layout.setContentsMargins(14, 14, 14, 14)
        lead_layout.setSpacing(10)

        lead_header = QHBoxLayout()
        lead_title = QLabel("aichs · Lead agent")
        lead_title.setStyleSheet(title_label_style(font_weight="bold"))
        lead_header.addWidget(lead_title)
        lead_header.addStretch()
        lead_status = QLabel("Always active")
        lead_status.setStyleSheet(status_pill_style(tone="accent", font_pt=12))
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
        prompt_label.setStyleSheet(title_label_style(font_weight="bold"))
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
            enabled.setStyleSheet(self._strong_checkbox_style)
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

    def _page_user_kit(self) -> QWidget:
        page, layout = self._page_shell(
            "User Kit",
            "Export or import portable AICHS personalization packages.",
        )
        palette()
        hint = QLabel(
            "YUK packages can include prompts, personality, crew preferences, skills, "
            "extensions, extension enabled state, and selected avatars. Models, API keys, "
            "conversations, and runtime state are always left out."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(self._hint_style)
        layout.addWidget(hint)

        actions = QFrame()
        actions.setStyleSheet(
            surface_frame_style()
        )
        action_layout = QVBoxLayout(actions)
        action_layout.setContentsMargins(14, 14, 14, 14)
        action_layout.setSpacing(10)

        export_btn = QPushButton("Export YUK...")
        export_btn.setStyleSheet(primary_button_style())
        export_btn.clicked.connect(self._export_yuk)
        self._yuk_export_btn = export_btn
        action_layout.addWidget(export_btn)

        import_btn = QPushButton("Import YUK...")
        import_btn.setStyleSheet(self._btn_style)
        import_btn.clicked.connect(self._import_yuk)
        self._yuk_import_btn = import_btn
        action_layout.addWidget(import_btn)

        status = QLabel("")
        status.setWordWrap(True)
        status.setStyleSheet(self._hint_style)
        self._yuk_export_status = status
        action_layout.addWidget(status)

        layout.addWidget(actions)
        layout.addStretch()
        self._sync_yuk_export_state()
        return page

    # ── logic ─────────────────────────────────────────────────────────────

    def _on_nav(self, row: int):
        if row >= 0:
            self._ensure_page(row)
            self._stack.setCurrentIndex(row)

    def _load_page_values(self, page_id: str, saved: dict):
        if page_id == "general":
            self._load_general_values(saved)
        elif page_id == "editor":
            self._load_editor_values(saved)
        elif page_id == "canvas":
            self._load_canvas_values(saved)
        elif page_id == "prompts":
            self._load_prompts_values(saved)
        elif page_id == "models":
            self._load_models_values(saved)
        elif page_id == "crew":
            self._load_crew_values(saved)

    def _load_general_values(self, saved: dict):
        theme = saved.get("theme", DEFAULT_THEME)
        if theme in ("dark", "modern", "light"):
            self.theme_combo.setCurrentText(theme)

        font = saved.get("font_size", DEFAULT_FONT_SIZE)
        if font in ("small", "medium", "large"):
            self.font_combo.setCurrentText(font)

        self.enter_to_send_check.setChecked(bool(saved.get("enter_to_send", False)))
        resume_mode = resume_session(saved)
        resume_index = self.resume_session_combo.findData(resume_mode)
        if resume_index < 0:
            resume_index = self.resume_session_combo.findData(DEFAULT_RESUME_SESSION)
        self.resume_session_combo.setCurrentIndex(max(0, resume_index))
        self.trash_retention_spin.setValue(trash_retention_days(saved))

    def _load_editor_values(self, saved: dict):
        self.file_editor_auto_save_check.setChecked(
            bool(saved.get(FILE_EDITOR_AUTO_SAVE_KEY, False))
        )
        self.file_editor_tab_spaces_spin.setValue(file_editor_tab_spaces(saved))

    def _load_canvas_values(self, saved: dict):
        strategy_index = self.canvas_generation_strategy_combo.findData(graph_generation_strategy(saved))
        if strategy_index < 0:
            strategy_index = self.canvas_generation_strategy_combo.findData(DEFAULT_GRAPH_GENERATION_STRATEGY)
        self.canvas_generation_strategy_combo.setCurrentIndex(max(0, strategy_index))

        run_mode_index = self.canvas_run_mode_combo.findData(canvas_run_mode(saved))
        if run_mode_index < 0:
            run_mode_index = self.canvas_run_mode_combo.findData(DEFAULT_CANVAS_RUN_MODE)
        self.canvas_run_mode_combo.setCurrentIndex(max(0, run_mode_index))
        self.canvas_parallel_limit_spin.setValue(canvas_parallel_limit(saved))
        approve_index = self.canvas_action_auto_approve_combo.findData(canvas_action_auto_approve(saved))
        if approve_index < 0:
            approve_index = self.canvas_action_auto_approve_combo.findData(DEFAULT_CANVAS_ACTION_AUTO_APPROVE)
        self.canvas_action_auto_approve_combo.setCurrentIndex(max(0, approve_index))
        self._sync_canvas_parallel_limit_enabled()

    def _sync_canvas_parallel_limit_enabled(self):
        if not hasattr(self, "canvas_run_mode_combo") or not hasattr(self, "canvas_parallel_limit_spin"):
            return
        is_parallel = str(self.canvas_run_mode_combo.currentData() or DEFAULT_CANVAS_RUN_MODE) == "parallel"
        self.canvas_parallel_limit_spin.setEnabled(is_parallel)

    def _load_prompts_values(self, saved: dict):
        self.file_review_prompt_template.setText(
            self._load_prompt_override(
                saved, FILE_REVIEW_PROMPT_TEMPLATE_KEY, DEFAULT_FILE_REVIEW_PROMPT_TEMPLATE,
            )
        )
        self.diagnostic_fix_prompt_template.setText(
            self._load_prompt_override(
                saved,
                DIAGNOSTIC_FIX_PROMPT_TEMPLATE_KEY,
                DEFAULT_DIAGNOSTIC_FIX_PROMPT_TEMPLATE,
            )
        )
        self.git_fix_prompt_template.setText(
            self._load_prompt_override(
                saved, GIT_FIX_PROMPT_TEMPLATE_KEY, DEFAULT_GIT_FIX_PROMPT_TEMPLATE,
            )
        )
        self.compact_resume_prompt.setText(
            self._load_prompt_override(
                saved, COMPACT_RESUME_PROMPT_KEY, DEFAULT_COMPACT_RESUME_PROMPT,
            )
        )
        self.auto_title_prompt_instructions.setPlainText(
            self._load_prompt_override(
                saved,
                AUTO_TITLE_PROMPT_INSTRUCTIONS_KEY,
                DEFAULT_AUTO_TITLE_PROMPT_INSTRUCTIONS,
            )
        )
        self.graph_agent_prompt.setPlainText(
            self._load_prompt_override(saved, GRAPH_AGENT_PROMPT_KEY, DEFAULT_GRAPH_AGENT_PROMPT)
        )
        self.compaction_summary_guidance.setPlainText(
            self._load_prompt_override(saved, COMPACTION_SUMMARY_GUIDANCE_KEY)
        )
        self.archivist_prompt.setPlainText(
            self._load_prompt_override(saved, ARCHIVIST_PROMPT_KEY, DEFAULT_ARCHIVIST_PROMPT)
        )
        self.commit_message_guidance.setPlainText(
            self._load_prompt_override(saved, COMMIT_MESSAGE_PROMPT_ADDITION_KEY)
        )

    def _load_models_values(self, saved: dict):
        defaults = saved.get("default_models", {})
        self._providers = self._load_configured_providers(saved)
        self._refresh_provider_table(defaults)

        compaction = saved.get("compaction") if isinstance(saved.get("compaction"), dict) else {}
        reserve = compaction.get("reserve_tokens", compaction.get("reserveTokens"))
        keep = compaction.get("keep_recent_tokens", compaction.get("keepRecentTokens"))
        self.compaction_reserve_edit.setText("" if reserve is None else str(int(reserve)))
        self.compaction_keep_recent_edit.setText("" if keep is None else str(int(keep)))

        self._update_compaction_label()

    def _load_crew_values(self, saved: dict):
        self.system_prompt.setPlainText(saved.get("system_prompt", SYSTEM_PROMPT))

    def _export_yuk(self):
        self._ensure_yuk_pages()
        if self._has_unsaved_yuk_changes():
            QMessageBox.warning(
                self,
                "Save settings first",
                "You have unsaved prompt, crew, or avatar changes. Save Settings before exporting a YUK package so those changes are included.",
            )
            return
        dialog = _YukExportDialog(self._cwd, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected = dialog.selected_item_ids()
        if not selected:
            QMessageBox.warning(self, "Nothing selected", "Select at least one item to export.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export YUK",
            str(Path.home() / "aichs-profile.yuk"),
            "YUK packages (*.yuk)",
        )
        if not path:
            return
        self._start_yuk_export(path, selected)

    def _start_yuk_export(self, path: str, selected_item_ids: set[str]) -> None:
        if self._yuk_export_active:
            return
        self._yuk_export_generation += 1
        generation = self._yuk_export_generation
        self._yuk_export_active = True
        self._sync_yuk_export_state("Exporting YUK package...")
        worker = _YukExportPackageWorker(generation, path, self._cwd, selected_item_ids)
        self._yuk_export_worker = worker
        worker.signals.done.connect(self._on_yuk_export_done)
        self._yuk_export_pool.start(worker)

    def _on_yuk_export_done(self, generation: int, _manifest: object, error: str) -> None:
        if generation != self._yuk_export_generation:
            return
        self._yuk_export_worker = None
        self._yuk_export_active = False
        if error:
            self._sync_yuk_export_state("Export failed.")
            QMessageBox.warning(self, "Export failed", error)
            return
        self._sync_yuk_export_state("YUK package exported.")
        QMessageBox.information(self, "Exported", "YUK package exported.")

    def _sync_yuk_export_state(self, status: str = "") -> None:
        busy = self._yuk_export_active or self._yuk_import_active
        if self._yuk_export_btn is not None:
            self._yuk_export_btn.setEnabled(not busy)
            self._yuk_export_btn.setText("Exporting..." if self._yuk_export_active else "Export YUK...")
        if self._yuk_import_btn is not None:
            self._yuk_import_btn.setEnabled(not busy)
            self._yuk_import_btn.setText("Importing..." if self._yuk_import_active else "Import YUK...")
        if self._yuk_export_status is not None:
            self._yuk_export_status.setText(status)

    def _cancel_yuk_export(self) -> None:
        self._yuk_export_generation += 1
        worker = self._yuk_export_worker
        self._yuk_export_worker = None
        self._yuk_export_active = False
        if worker is not None:
            worker.cancel()
        self._sync_yuk_export_state("")

    def reject(self) -> None:
        self._cancel_yuk_export()
        super().reject()

    def closeEvent(self, event) -> None:
        self._cancel_yuk_export()
        super().closeEvent(event)

    def _import_yuk(self):
        if self._yuk_import_active or self._yuk_export_active:
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import YUK",
            "",
            "YUK packages (*.yuk)",
        )
        if not path:
            return
        self._start_yuk_inspect(path)

    def _start_yuk_inspect(self, path: str) -> None:
        self._yuk_import_generation += 1
        generation = self._yuk_import_generation
        self._yuk_import_active = True
        self._sync_yuk_export_state("Inspecting YUK package...")
        worker = _YukInspectWorker(generation, path, self._cwd)
        worker.signals.done.connect(self._on_yuk_inspect_done)
        self._yuk_import_pool.start(worker)

    def _on_yuk_inspect_done(self, generation: int, path: str, inspection: object, error: str) -> None:
        if generation != self._yuk_import_generation:
            return
        if error:
            self._yuk_import_active = False
            self._sync_yuk_export_state("Import failed.")
            QMessageBox.warning(self, "Import failed", error)
            return
        dialog = _YukImportDialog(inspection, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self._yuk_import_active = False
            self._sync_yuk_export_state("")
            return
        self._start_yuk_apply(path, dialog.choices())

    def _start_yuk_apply(self, path: str, choices: dict[str, str]) -> None:
        generation = self._yuk_import_generation
        self._sync_yuk_export_state("Importing YUK package...")
        worker = _YukApplyWorker(generation, path, self._cwd, choices)
        worker.signals.done.connect(self._on_yuk_apply_done)
        self._yuk_import_pool.start(worker)

    def _on_yuk_apply_done(self, generation: int, _result: object, error: str) -> None:
        if generation != self._yuk_import_generation:
            return
        self._yuk_import_active = False
        if error:
            self._sync_yuk_export_state("Import failed.")
            QMessageBox.warning(self, "Import failed", error)
            return
        clear_cache()
        self._saved = self.store.load()
        self._sync_yuk_export_state("YUK package imported.")
        QMessageBox.information(self, "Imported", "YUK package imported.")
        self.accept()

    def _ensure_yuk_pages(self) -> None:
        for page_id in ("general", "prompts", "crew"):
            if page_id in self._page_ids:
                self._ensure_page(self._page_ids.index(page_id))

    def _has_unsaved_yuk_changes(self) -> bool:
        return self._current_yuk_settings() != self._saved_yuk_settings(self.store.load())

    def _saved_yuk_settings(self, saved: dict) -> dict:
        return {
            "system_prompt": str(saved.get("system_prompt") or SYSTEM_PROMPT).strip() or SYSTEM_PROMPT,
            FILE_REVIEW_PROMPT_TEMPLATE_KEY: file_review_prompt_template(saved),
            DIAGNOSTIC_FIX_PROMPT_TEMPLATE_KEY: diagnostic_fix_prompt_template(saved),
            GIT_FIX_PROMPT_TEMPLATE_KEY: git_fix_prompt_template(saved),
            COMPACT_RESUME_PROMPT_KEY: compact_resume_prompt(saved),
            AUTO_TITLE_PROMPT_INSTRUCTIONS_KEY: auto_title_prompt_instructions(saved),
            GRAPH_AGENT_PROMPT_KEY: graph_agent_prompt(saved),
            COMPACTION_SUMMARY_GUIDANCE_KEY: compaction_summary_guidance(saved),
            ARCHIVIST_PROMPT_KEY: archivist_prompt(saved),
            COMMIT_MESSAGE_PROMPT_ADDITION_KEY: str(saved.get(COMMIT_MESSAGE_PROMPT_ADDITION_KEY, "")).strip(),
            "crew": _normalized_crew_settings(saved),
            "avatar_human": str(saved.get("avatar_human") or "human"),
            "avatar_agent": str(saved.get("avatar_agent") or "agent"),
        }

    def _current_yuk_settings(self) -> dict:
        crew = {}
        for member in all_crew():
            widgets = self._crew_widgets.get(member.id, {})
            if not widgets:
                continue
            model = widgets["model"].currentData() or ""
            crew[member.id] = {
                "enabled": widgets["enabled"].isChecked(),
                "model": model,
                "prompt": widgets["prompt"].toPlainText().strip(),
                "color": widgets["color"].value(),
                "avatar": widgets["portrait"].value(),
            }
        return {
            "system_prompt": self.system_prompt.toPlainText().strip() or SYSTEM_PROMPT,
            FILE_REVIEW_PROMPT_TEMPLATE_KEY: self.file_review_prompt_template.text().strip() or file_review_prompt_template({}),
            DIAGNOSTIC_FIX_PROMPT_TEMPLATE_KEY: self.diagnostic_fix_prompt_template.text().strip() or diagnostic_fix_prompt_template({}),
            GIT_FIX_PROMPT_TEMPLATE_KEY: self.git_fix_prompt_template.text().strip() or git_fix_prompt_template({}),
            COMPACT_RESUME_PROMPT_KEY: self.compact_resume_prompt.text().strip() or compact_resume_prompt({}),
            AUTO_TITLE_PROMPT_INSTRUCTIONS_KEY: self.auto_title_prompt_instructions.toPlainText().strip() or auto_title_prompt_instructions({}),
            GRAPH_AGENT_PROMPT_KEY: self.graph_agent_prompt.toPlainText().strip() or graph_agent_prompt({}),
            COMPACTION_SUMMARY_GUIDANCE_KEY: self.compaction_summary_guidance.toPlainText().strip(),
            ARCHIVIST_PROMPT_KEY: self.archivist_prompt.toPlainText().strip() or archivist_prompt({}),
            COMMIT_MESSAGE_PROMPT_ADDITION_KEY: self.commit_message_guidance.toPlainText().strip(),
            "crew": crew,
            "avatar_human": self.human_portrait.value(),
            "avatar_agent": self.agent_portrait.value(),
        }

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
        self._ensure_all_pages()
        data = self.store.load()
        configured_ids = {provider["id"] for provider in self._providers}
        provider_keys = {
            provider["id"]: provider.get("api_key", "").strip()
            for provider in self._providers
            if provider.get("api_key", "").strip()
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
            generation = _generation_values(provider)
            has_model_generation_override = any(
                _generation_values(model) for model in provider.get("models", [])
            )
            has_override = (
                bool(provider.get("base_url"))
                or not is_builtin
                or has_model_override
                or bool(generation)
                or has_model_generation_override
            )
            if not has_override:
                continue
            entry = {
                "api": provider.get("api", "openai-compatible"),
                "apiKey": provider.get("api_key_spec") or _provider_env_var(provider_id),
            }
            if provider.get("base_url"):
                entry["baseUrl"] = provider["base_url"]
            if generation.get("temperature") is not None:
                entry["temperature"] = generation["temperature"]
            if generation.get("top_k") is not None:
                entry["topK"] = generation["top_k"]
            if generation.get("min_p") is not None:
                entry["minP"] = generation["min_p"]
            if not is_builtin or has_model_override or has_model_generation_override:
                models = []
                for model in provider.get("models", []):
                    model_entry = {"id": model["id"]}
                    if model.get("name") and model["name"] != model["id"]:
                        model_entry["name"] = model["name"]
                    if not is_builtin:
                        window = _model_context_window(model)
                        if window:
                            model_entry["contextWindow"] = window
                    model_generation = _generation_values(model)
                    if model_generation.get("temperature") is not None:
                        model_entry["temperature"] = model_generation["temperature"]
                    if model_generation.get("top_k") is not None:
                        model_entry["topK"] = model_generation["top_k"]
                    if model_generation.get("min_p") is not None:
                        model_entry["minP"] = model_generation["min_p"]
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
            RESUME_SESSION_KEY: str(self.resume_session_combo.currentData() or DEFAULT_RESUME_SESSION),
            FILE_EDITOR_AUTO_SAVE_KEY: self.file_editor_auto_save_check.isChecked(),
            FILE_EDITOR_TAB_SPACES_KEY: self.file_editor_tab_spaces_spin.value(),
            GRAPH_GENERATION_STRATEGY_KEY: str(
                self.canvas_generation_strategy_combo.currentData() or DEFAULT_GRAPH_GENERATION_STRATEGY
            ),
            CANVAS_RUN_MODE_KEY: str(self.canvas_run_mode_combo.currentData() or DEFAULT_CANVAS_RUN_MODE),
            CANVAS_PARALLEL_LIMIT_KEY: self.canvas_parallel_limit_spin.value(),
            CANVAS_ACTION_AUTO_APPROVE_KEY: str(
                self.canvas_action_auto_approve_combo.currentData() or DEFAULT_CANVAS_ACTION_AUTO_APPROVE
            ),
            FILE_REVIEW_PROMPT_TEMPLATE_KEY: self.file_review_prompt_template.text().strip(),
            DIAGNOSTIC_FIX_PROMPT_TEMPLATE_KEY: self.diagnostic_fix_prompt_template.text().strip(),
            GIT_FIX_PROMPT_TEMPLATE_KEY: self.git_fix_prompt_template.text().strip(),
            COMPACT_RESUME_PROMPT_KEY: self.compact_resume_prompt.text().strip(),
            AUTO_TITLE_PROMPT_INSTRUCTIONS_KEY: self.auto_title_prompt_instructions.toPlainText().strip(),
            GRAPH_AGENT_PROMPT_KEY: self.graph_agent_prompt.toPlainText().strip(),
            COMPACTION_SUMMARY_GUIDANCE_KEY: self.compaction_summary_guidance.toPlainText().strip(),
            ARCHIVIST_PROMPT_KEY: self.archivist_prompt.toPlainText().strip(),
            TRASH_RETENTION_DAYS_KEY: self.trash_retention_spin.value(),
            COMMIT_MESSAGE_PROMPT_ADDITION_KEY: self.commit_message_guidance.toPlainText().strip(),
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
        model_registry.reload(refresh_anthropic=False)
        model_registry.refresh_anthropic_context_async()
        self.store.save(data)
        self.store.apply_saved(data)
        clear_cache()
        self.accept()
