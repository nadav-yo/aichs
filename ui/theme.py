from storage.settings import SettingsStore
import sys

ACCENT = "#528bff"
ACCENT_HOVER = "#6b9dff"
ACCENT_DIM = "#3a6fd4"
ACCENT_SOFT_DARK = "#1a2744"
ACCENT_SOFT_LIGHT = "#e8f0ff"

_PALETTES = {
    "dark": {
        "BG": "#0f0f11",
        "BG2": "#16161a",
        "BG3": "#1e1e24",
        "BORDER": "#2a2a32",
        "BORDER_SUBTLE": "#222228",
        "TEXT": "#ececf1",
        "TEXT_DIM": "#8b8b9a",
        "BUBBLE_AI": "#232329",
        "BUBBLE_AI_TEXT": "#ececf1",
        "INPUT_BG": "#1a1a20",
        "INPUT_TEXT": "#ececf1",
        "SELECTION": "#1e2a42",
        "SELECTION_TEXT": "#ececf1",
        "SUCCESS": "#34d399",
        "SUCCESS_BG": "#142820",
        "SUCCESS_BORDER": "#1f4034",
        "LINK": "#8ab4ff",
        "FILE_LINK": "#9cc9ff",
    },
    "modern": {
        "BG": "#111417",
        "BG2": "#171b1f",
        "BG3": "#20262b",
        "BORDER": "#313941",
        "BORDER_SUBTLE": "#252b31",
        "TEXT": "#eef2f1",
        "TEXT_DIM": "#9ea9b2",
        "BUBBLE_AI": "#1b2126",
        "BUBBLE_AI_TEXT": "#eef2f1",
        "INPUT_BG": "#181e23",
        "INPUT_TEXT": "#eef2f1",
        "SELECTION": "#263f3d",
        "SELECTION_TEXT": "#f5fffb",
        "SUCCESS": "#64d6a2",
        "SUCCESS_BG": "#14342a",
        "SUCCESS_BORDER": "#245646",
        "LINK": "#8ac7ff",
        "FILE_LINK": "#92ead4",
    },
    "light": {
        "BG": "#f5f5f7",
        "BG2": "#ffffff",
        "BG3": "#f0f0f4",
        "BORDER": "#e2e2e8",
        "BORDER_SUBTLE": "#ececf0",
        "TEXT": "#18181b",
        "TEXT_DIM": "#71717a",
        "BUBBLE_AI": "#f4f4f5",
        "BUBBLE_AI_TEXT": "#18181b",
        "INPUT_BG": "#ffffff",
        "INPUT_TEXT": "#18181b",
        "SELECTION": "#e8f0ff",
        "SELECTION_TEXT": "#18181b",
        "SUCCESS": "#16a34a",
        "SUCCESS_BG": "#ecfdf3",
        "SUCCESS_BORDER": "#bbf7d0",
        "LINK": ACCENT,
        "FILE_LINK": "#0969da",
    },
}

FONT_SIZES = {"small": 12, "medium": 14, "large": 17}
if sys.platform == "darwin":
    UI_FONT = "Helvetica Neue"
    FONT_FAMILY = '"Helvetica Neue", "Segoe UI", sans-serif'
elif sys.platform == "win32":
    UI_FONT = "Segoe UI"
    FONT_FAMILY = '"Segoe UI", sans-serif'
else:
    UI_FONT = "Sans Serif"
    FONT_FAMILY = "sans-serif"
MONO_FONT = "Menlo"
MONO_FONT_CSS = "Menlo, 'SF Mono', 'Cascadia Code', 'Courier New', monospace"
DEFAULT_THEME = "dark"
DEFAULT_FONT_SIZE = "medium"
DEFAULT_COMPACTION_THRESHOLD_PCT = 90

# Legacy module-level aliases (dark defaults for imports at load time)
_p0 = _PALETTES["dark"]
BG = _p0["BG"]
BG2 = _p0["BG2"]
BG3 = _p0["BG3"]
BORDER = _p0["BORDER"]
TEXT = _p0["TEXT"]
TEXT_DIM = _p0["TEXT_DIM"]


def current_theme() -> str:
    theme = SettingsStore().load().get("theme", DEFAULT_THEME)
    return theme if theme in _PALETTES else DEFAULT_THEME


def palette(theme: str | None = None) -> dict:
    name = theme or current_theme()
    return _PALETTES.get(name, _PALETTES[DEFAULT_THEME])


def chat_font_pt(size_name: str | None = None) -> int:
    if size_name is None:
        size_name = SettingsStore().load().get("font_size", DEFAULT_FONT_SIZE)
    return FONT_SIZES.get(size_name, FONT_SIZES[DEFAULT_FONT_SIZE])


def meta_font_pt(size_name: str | None = None) -> int:
    """Secondary text (timestamps, token count, labels)."""
    return max(10, chat_font_pt(size_name) - 3)


def mono_font_pt(size_name: str | None = None) -> int:
    """Monospace areas (git, terminal, code viewer)."""
    return max(11, chat_font_pt(size_name) - 1)


def app_font(size_name: str | None = None):
    from PyQt6.QtGui import QFont, QFontDatabase
    from PyQt6.QtWidgets import QApplication

    if QApplication.instance():
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
    else:
        font = QFont(UI_FONT)
    font.setPointSize(chat_font_pt(size_name))
    return font


def mono_font(size_pt: int | None = None):
    from PyQt6.QtGui import QFont
    return QFont(MONO_FONT, size_pt or mono_font_pt())


def list_selection_bg(theme: str | None = None) -> str:
    return palette(theme)["SELECTION"]


def markdown_css(font_pt: int | None = None, theme: str | None = None) -> str:
    p = palette(theme)
    fs = font_pt or chat_font_pt()
    is_light = (theme or current_theme()) == "light"
    code_bg = "rgba(0,0,0,0.06)" if is_light else "rgba(255,255,255,0.08)"
    quote_border = ACCENT if is_light else "#4a5568"
    link = p["LINK"]
    file_link = p["FILE_LINK"]
    return (
        f"body {{ margin:0; padding:0; color:{p['BUBBLE_AI_TEXT']}; font-size:{fs}px;"
        f"line-height:1.55; font-family:{FONT_FAMILY}; }}"
        f"a {{ color:{link}; text-decoration:none; }}"
        f"a.aicc-file-link {{ color:{file_link}; text-decoration:underline;"
        "text-decoration-thickness:1px; text-underline-offset:2px; }"
        f"code {{ background:{code_bg}; border-radius:5px;"
        f"padding:2px 6px; font-family:{MONO_FONT_CSS}; font-size:{max(10, fs - 2)}px; }}"
        "h1,h2,h3 { margin:8px 0 4px 0; font-weight:600; }"
        "ul,ol { margin:6px 0; padding-left:20px; }"
        "p { margin:4px 0; }"
        f"blockquote {{ margin:6px 0 6px 4px; padding-left:12px;"
        f"border-left:3px solid {quote_border}; color:{p['TEXT_DIM']}; }}"
    )


def markdown_file_link_style(theme: str | None = None) -> str:
    p = palette(theme)
    return (
        f"color:{p['FILE_LINK']};"
        "text-decoration:underline;"
        "text-decoration-thickness:1px;"
        "text-underline-offset:2px;"
    )


def card_frame_style() -> str:
    p = palette()
    return (
        f"QFrame {{ background:{p['BG3']}; border:1px solid {p['BORDER']};"
        f"border-radius:10px; }}"
    )


def tool_notice_style() -> str:
    p = palette()
    fs = meta_font_pt()
    return (
        f"color:{p['TEXT_DIM']}; font-size:{fs}px; font-family:{MONO_FONT_CSS};"
        f"background:{p['BG3']}; padding:6px 12px; border:1px solid {p['BORDER']};"
        f"border-radius:8px; margin:4px 16px;"
    )


def center_notice_style() -> str:
    p = palette()
    return f"color:{p['TEXT_DIM']}; font-size:{meta_font_pt()}px; padding:8px;"


def agents_banner_style() -> str:
    p = palette()
    return (
        f"QLabel {{ background:{p['SUCCESS_BG']}; color:{p['SUCCESS']};"
        f"font-size:{meta_font_pt()}px; padding:6px 12px;"
        f"border-bottom:1px solid {p['SUCCESS_BORDER']}; }}"
    )


def input_bar_style() -> str:
    p = palette()
    return (
        f"QFrame {{ background:{p['BG2']}; border-top:1px solid {p['BORDER_SUBTLE']};"
        f"padding:2px 0; }}"
    )


def _pill_button_style(bg: str, hover: str, disabled_bg: str, disabled_fg: str) -> str:
    fs = max(12, chat_font_pt())
    return (
        f"QPushButton {{ background:{bg}; color:white; border:none;"
        f"border-radius:20px; padding:0 22px; font-size:{fs}px; font-weight:600; }}"
        f"QPushButton:hover {{ background:{hover}; }}"
        f"QPushButton:disabled {{ background:{disabled_bg}; color:{disabled_fg}; }}"
    )


def send_button_style() -> str:
    return _pill_button_style(ACCENT, ACCENT_HOVER, ACCENT_DIM, "#9bb8e8")


def stop_button_style() -> str:
    return _pill_button_style("#dc2626", "#ef4444", "#7f1d1d", "#fca5a5")


def floating_button_style() -> str:
    meta = meta_font_pt()
    return _pill_button_style(ACCENT, ACCENT_HOVER, ACCENT_DIM, "#9bb8e8").replace(
        f"font-size:{max(12, chat_font_pt())}px;",
        f"font-size:{max(14, meta + 2)}px;",
    )


def new_chat_button_style() -> str:
    p = palette()
    fs = chat_font_pt()
    soft = ACCENT_SOFT_LIGHT if current_theme() == "light" else ACCENT_SOFT_DARK
    return (
        f"QPushButton {{ background:{soft}; color:{ACCENT}; border:none;"
        f"border-radius:10px; margin:10px 12px 6px 12px; padding:10px 14px;"
        f"font-size:{fs}px; font-weight:600; }}"
        f"QPushButton:hover {{ background:{ACCENT}; color:white; }}"
    )


def icon_button_style(size_px: int = 28) -> str:
    p = palette()
    return (
        f"QPushButton {{ background:transparent; color:{p['TEXT_DIM']}; border:none;"
        f"border-radius:{size_px // 2}px; padding:0; font-size:15px;"
        f"min-width:{size_px}px; max-width:{size_px}px;"
        f"min-height:{size_px}px; max-height:{size_px}px; }}"
        f"QPushButton:hover {{ background:{p['BG3']}; color:{p['TEXT']}; }}"
        f"QPushButton:pressed {{ background:{p['BORDER']}; }}"
    )


def files_header_style() -> str:
    p = palette()
    meta = meta_font_pt()
    return (
        f"QWidget#filesHeader {{ background:{p['BG2']};"
        f"border-bottom:1px solid {p['BORDER_SUBTLE']}; }}"
        f"QLabel#filesPath {{ color:{p['TEXT_DIM']}; font-size:{meta}px;"
        f"background:transparent; padding:0; }}"
    )


def search_field_style() -> str:
    p = palette()
    fs = chat_font_pt()
    return (
        f"QLineEdit {{ background:{p['BG3']}; color:{p['TEXT']};"
        f"border:1px solid {p['BORDER_SUBTLE']}; border-radius:10px;"
        f"margin:0 12px 8px 12px; padding:8px 12px; font-size:{fs}px; }}"
        f"QLineEdit:focus {{ border:1px solid {ACCENT}; }}"
    )


def conversation_list_style() -> str:
    p = palette()
    sel = list_selection_bg()
    return (
        f"QListWidget {{ background:{p['BG2']}; border:none; outline:none; }}"
        f"QListWidget::item {{ border:none; border-radius:8px; margin:1px 4px; }}"
        f"QListWidget::item:hover {{ background:{p['BG3']}; }}"
        f"QListWidget::item:selected {{ background:{sel}; color:{p['SELECTION_TEXT']}; }}"
    )


def timestamp_style() -> str:
    p = palette()
    return (
        f"color:{p['TEXT_DIM']}; font-size:{max(9, chat_font_pt() - 4)}px;"
        "background:transparent; padding:0 4px;"
    )


def compaction_threshold_pct() -> int:
    pct = SettingsStore().load().get("compaction_threshold_pct", DEFAULT_COMPACTION_THRESHOLD_PCT)
    try:
        pct = int(pct)
    except (TypeError, ValueError):
        pct = DEFAULT_COMPACTION_THRESHOLD_PCT
    return max(60, min(95, pct))


def build_stylesheet(theme: str | None = None) -> str:
    p = palette(theme)
    fs = chat_font_pt()
    meta = meta_font_pt()
    sel = p["SELECTION"]
    return f"""
QWidget          {{ background:{p["BG"]}; color:{p["TEXT"]}; font-size:{fs}px; }}
QMainWindow      {{ background:{p["BG"]}; }}
QSplitter        {{ background:{p["BG"]}; }}
QSplitter::handle {{ background:{p["BORDER_SUBTLE"]}; width:1px; }}
QSplitter::handle:hover {{ background:{p["BORDER"]}; }}
QScrollArea      {{ background:{p["BG"]}; border:none; }}
QScrollBar:vertical {{
    background:transparent; width:6px; border:none; margin:2px 0;
}}
QScrollBar::handle:vertical {{
    background:{p["BORDER"]}; border-radius:3px; min-height:24px;
}}
QScrollBar::handle:vertical:hover {{ background:{p["TEXT_DIM"]}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
QScrollBar:horizontal {{
    background:transparent; height:6px; border:none; margin:0 2px;
}}
QScrollBar::handle:horizontal {{
    background:{p["BORDER"]}; border-radius:3px; min-width:24px;
}}
QScrollBar::handle:horizontal:hover {{ background:{p["TEXT_DIM"]}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width:0; }}
QListWidget      {{ background:{p["BG2"]}; border:none; font-size:{fs}px; color:{p["TEXT"]}; }}
QListWidget::item {{ padding:6px 8px; border:none; border-radius:6px; margin:1px 4px; }}
QListWidget::item:hover {{ background:{p["BG3"]}; }}
QListWidget::item:selected {{ background:{sel}; color:{p["SELECTION_TEXT"]}; }}
QTextEdit        {{ background:{p["BG2"]}; border:none; color:{p["TEXT"]}; font-size:{fs}px; }}
QLineEdit        {{
    background:{p["BG3"]}; color:{p["TEXT"]}; border:1px solid {p["BORDER"]};
    border-radius:8px; padding:6px 10px; font-size:{fs}px;
}}
QLineEdit:focus {{ border:1px solid {ACCENT}; }}
QComboBox        {{
    background:{p["BG3"]}; color:{p["TEXT"]}; border:1px solid {p["BORDER"]};
    border-radius:8px; padding:4px 10px; font-size:{fs}px;
}}
QComboBox:hover {{ border:1px solid {p["TEXT_DIM"]}; }}
QComboBox::drop-down {{ border:none; width:20px; }}
QComboBox QAbstractItemView {{
    background:{p["BG3"]}; color:{p["TEXT"]}; border:1px solid {p["BORDER"]};
    border-radius:8px; selection-background-color:{sel}; font-size:{fs}px;
}}
QLabel           {{ background:transparent; color:{p["TEXT"]}; font-size:{fs}px; }}
QPushButton      {{
    background:{p["BG3"]}; color:{p["TEXT"]}; border:1px solid {p["BORDER"]};
    border-radius:8px; padding:6px 12px; font-size:{fs}px;
}}
QPushButton:hover {{ background:{p["BORDER"]}; border-color:{p["TEXT_DIM"]}; }}
QPushButton:pressed {{ background:{p["BG2"]}; }}
QPushButton:disabled {{ background:{p["BG2"]}; color:{p["TEXT_DIM"]}; border-color:{p["BORDER_SUBTLE"]}; }}
QTabWidget::pane {{ border:none; background:{p["BG2"]}; top:-1px; }}
QTabBar::tab {{
    background:transparent; color:{p["TEXT_DIM"]}; padding:8px 16px;
    border:none; border-bottom:2px solid transparent; font-size:{meta}px;
    font-weight:500; margin:0 2px;
}}
QTabBar::tab:selected {{ color:{p["TEXT"]}; border-bottom:2px solid {ACCENT}; font-weight:600; }}
QTabBar::tab:hover {{ color:{p["TEXT"]}; background:{p["BG3"]}; border-radius:6px 6px 0 0; }}
QTreeWidget      {{ background:{p["BG2"]}; border:none; font-size:{fs}px; color:{p["TEXT"]}; }}
QTreeWidget::item {{ padding:3px 2px; border-radius:4px; }}
QTreeWidget::item:hover {{ background:{p["BG3"]}; }}
QTreeWidget::item:selected {{ background:{sel}; color:{p["SELECTION_TEXT"]}; }}
QHeaderView::section {{
    background:{p["BG2"]}; color:{p["TEXT_DIM"]}; border:none;
    font-size:{meta}px; padding:6px 8px; font-weight:500;
}}
QDialog          {{ background:{p["BG2"]}; color:{p["TEXT"]}; font-size:{fs}px; }}
QMenu            {{
    background:{p["BG3"]}; color:{p["TEXT"]}; border:1px solid {p["BORDER"]};
    border-radius:8px; padding:4px;
}}
QMenu::item {{ padding:6px 24px 6px 12px; border-radius:4px; }}
QMenu::item:selected {{ background:{sel}; }}
QSlider::groove:horizontal {{ background:{p["BORDER"]}; height:4px; border-radius:2px; }}
QSlider::handle:horizontal {{
    background:{ACCENT}; width:16px; height:16px; margin:-6px 0; border-radius:8px;
}}
QToolTip {{
    background:{p["BG3"]}; color:{p["TEXT"]}; border:1px solid {p["BORDER"]};
    border-radius:6px; padding:4px 8px; font-size:{meta}px;
}}
"""


def bubble_label_style(is_user: bool, font_pt: int | None = None) -> str:
    p = palette()
    fs = font_pt or chat_font_pt()
    if is_user:
        return (
            f"background:{ACCENT}; color:white; padding:10px 16px;"
            f"border-radius:18px; font-size:{fs}px; line-height:1.45;"
        )
    return (
        f"background:{p['BUBBLE_AI']}; color:{p['BUBBLE_AI_TEXT']}; padding:10px 16px;"
        f"border:1px solid {p['BORDER_SUBTLE']}; border-radius:18px; font-size:{fs}px;"
        f"line-height:1.45;"
    )


def composer_style(font_pt: int | None = None) -> str:
    p = palette()
    fs = font_pt or chat_font_pt()
    return (
        f"QTextEdit {{ background:{p['INPUT_BG']}; color:{p['INPUT_TEXT']};"
        f"border:1px solid {p['BORDER']}; border-radius:14px;"
        f"padding:10px 16px; font-size:{fs}px; selection-background-color:{ACCENT}; }}"
        f"QTextEdit:focus {{ border:1px solid {ACCENT}; }}"
    )


def edit_bubble_style(font_pt: int | None = None) -> str:
    p = palette()
    fs = font_pt or chat_font_pt()
    return (
        f"background:{p['BG3']}; color:{p['TEXT']}; border:1px solid {ACCENT};"
        f"border-radius:10px; padding:8px 12px; font-size:{fs}px;"
    )


def apply_app_theme(app, theme: str | None = None) -> None:
    app.setFont(app_font())
    app.setStyleSheet(build_stylesheet(theme))


DARK_STYLE = build_stylesheet("dark")
