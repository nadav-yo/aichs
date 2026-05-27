from storage.settings import SettingsStore
import sys

ACCENT = "#528bff"
ACCENT_HOVER = "#6b9dff"
ACCENT_DIM = "#3a6fd4"
ACCENT_SOFT_DARK = "#1a2744"
ACCENT_SOFT_LIGHT = "#e8f0ff"

_CREW_TONES = {
    "scout": {
        "dark": ("#132332", "#275f87", "#7dd3fc"),
        "modern": ("#132832", "#2b697c", "#67e8f9"),
        "light": ("#e8f6ff", "#9ed8f4", "#0369a1"),
    },
    "archivist": {
        "dark": ("#282415", "#806c27", "#facc15"),
        "modern": ("#292514", "#8b762b", "#fde047"),
        "light": ("#fff9db", "#fde68a", "#a16207"),
    },
}

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
    UI_FONT = "Aptos"
    FONT_FAMILY = '"Aptos", "Segoe UI Variable Text", "Segoe UI", sans-serif'
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


def git_status_color(code: str) -> str:
    """Foreground color for a `git status --short` two-letter code."""
    p = palette()
    if code in ("??", "A ", "A"):
        return p["SUCCESS"] if code != "??" else p["TEXT_DIM"]
    if "D" in code:
        return "#f87171"
    if "M" in code or "U" in code:
        return ACCENT
    return p["TEXT"]


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

    font = QFont(UI_FONT)
    if QApplication.instance():
        families = set(QFontDatabase.families())
        for family in (
            UI_FONT, "Aptos Display", "Segoe UI Variable Text",
            "Segoe UI Variable Display", "Segoe UI", "Helvetica Neue",
        ):
            if family in families:
                font = QFont(family)
                break
        else:
            font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
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
    code_bg = "#eef2ff" if is_light else "#182232"
    code_fg = "#27272a" if is_light else "#f1f5ff"
    quote_border = ACCENT if is_light else "#4a5568"
    link = p["LINK"]
    file_link = p["FILE_LINK"]
    return (
        f"body {{ margin:0; padding:0; color:{p['BUBBLE_AI_TEXT']}; font-size:{fs}px;"
        f"line-height:1.58; font-family:{FONT_FAMILY}; }}"
        f"a {{ color:{link}; text-decoration:none; }}"
        f"a.aichs-file-link {{ color:{file_link}; text-decoration:none; }}"
        f"code {{ background:{code_bg}; border-radius:4px;"
        f"padding:1px 4px; color:{code_fg}; font-family:{MONO_FONT_CSS};"
        f"font-size:{max(10, fs - 1)}px; }}"
        f"pre {{ background:rgba(255,255,255,0.04); border-radius:8px; padding:8px 10px;"
        "white-space:pre-wrap; margin:6px 0; }}"
        "pre code { background:transparent; padding:0; border-radius:0; }"
        "h1,h2,h3 { margin:8px 0 4px 0; font-weight:600; }"
        "ul,ol { margin:8px 0; padding-left:22px; }"
        "li { margin:3px 0; padding-left:2px; }"
        "p { margin:4px 0; }"
        f"blockquote {{ margin:6px 0 6px 4px; padding-left:12px;"
        f"border-left:3px solid {quote_border}; color:{p['TEXT_DIM']}; }}"
    )


def markdown_file_link_style(theme: str | None = None) -> str:
    p = palette(theme)
    is_light = (theme or current_theme()) == "light"
    bg = "#e8f0ff" if is_light else "#1a2c48"
    fg = "#0f4fa8" if is_light else "#b7d5ff"
    return (
        f"color:{fg}; background:{bg};"
        "text-decoration:none; border-radius:5px; padding:1px 5px;"
        "font-weight:600;"
    )


def user_reference_style() -> str:
    return (
        "color:#f8fbff; background:#5b86df;"
        "text-decoration:none; border-radius:5px; padding:1px 5px;"
        "font-weight:600;"
    )


def composer_reference_colors() -> dict:
    if current_theme() == "light":
        return {"fg": "#0f3f94", "bg": "#e8f0ff"}
    return {"fg": "#dbeafe", "bg": "#1c3154"}


def card_frame_style() -> str:
    p = palette()
    bg = {
        "dark": "#121720",
        "modern": "#131a22",
    }.get(current_theme(), p["BG3"])
    border = {
        "dark": "#202a34",
        "modern": "#26313b",
    }.get(current_theme(), p["BORDER"])
    return (
        f"QFrame {{ background:{bg}; border:1px solid {border};"
        f"border-radius:8px; }}"
    )


def tool_notice_style() -> str:
    p = palette()
    fs = meta_font_pt()
    return (
        f"color:{p['TEXT_DIM']}; font-size:{fs}px; font-family:{FONT_FAMILY};"
        "background:transparent; padding:2px 0; border:none;"
    )


def separator_color() -> str:
    p = palette()
    return {
        "dark": "#1a1d23",
        "modern": "#202832",
    }.get(current_theme(), p["BORDER_SUBTLE"])


def center_notice_style() -> str:
    p = palette()
    return f"color:{p['TEXT_DIM']}; font-size:{meta_font_pt()}px; padding:8px;"


def input_bar_style() -> str:
    p = palette()
    return (
        f"QFrame {{ background:{p['BG']}; border-top:1px solid {separator_color()}; }}"
    )


def _pill_button_style(
    bg: str,
    hover: str,
    pressed: str,
    disabled_bg: str,
    disabled_fg: str,
) -> str:
    fs = max(12, chat_font_pt())
    height = 38
    radius = height // 2
    return (
        f"QPushButton {{ background:{bg}; color:white; border:none;"
        f"border-radius:{radius}px; padding:0 24px; font-size:{fs}px; font-weight:600;"
        f"min-width:76px; min-height:{height}px; max-height:{height}px; }}"
        f"QPushButton:hover {{ background:{hover}; }}"
        f"QPushButton:pressed {{ background:{pressed}; }}"
        f"QPushButton:disabled {{ background:{disabled_bg}; color:{disabled_fg}; }}"
    )


def send_button_style() -> str:
    return _composer_action_button_style(ACCENT_DIM, ACCENT, "#2f5fb8", ACCENT_SOFT_DARK, "#8daee8")


def stop_button_style() -> str:
    return _composer_action_button_style("#dc2626", "#ef4444", "#b91c1c", "#7f1d1d", "#fca5a5")


def _composer_action_button_style(
    bg: str,
    hover: str,
    pressed: str,
    disabled_bg: str,
    disabled_fg: str,
) -> str:
    fs = max(11, chat_font_pt() - 1)
    height = 32
    radius = 9
    return (
        f"QPushButton {{ background:{bg}; color:white; border:none;"
        f"border-radius:{radius}px; padding:0 10px; font-size:{fs}px; font-weight:500;"
        f"font-family:{FONT_FAMILY};"
        f"min-width:64px; min-height:{height}px; max-height:{height}px; }}"
        f"QPushButton:hover {{ background:{hover}; }}"
        f"QPushButton:pressed {{ background:{pressed}; }}"
        f"QPushButton:disabled {{ background:{disabled_bg}; color:{disabled_fg}; }}"
    )


def floating_button_style() -> str:
    meta = meta_font_pt()
    return _pill_button_style(ACCENT, ACCENT_HOVER, ACCENT_DIM, ACCENT_DIM, "#9bb8e8").replace(
        f"font-size:{max(12, chat_font_pt())}px;",
        f"font-size:{max(14, meta + 2)}px;",
    )


def new_chat_button_style() -> str:
    p = palette()
    fs = max(12, chat_font_pt() - 1)
    soft = "#edf4ff" if current_theme() == "light" else "#182847"
    return (
        f"QPushButton {{ background:{soft}; color:{ACCENT}; border:none;"
        f"border-radius:8px; margin:8px 14px 6px 14px; padding:7px 12px;"
        f"font-size:{fs}px; font-weight:600; }}"
        f"QPushButton:hover {{ background:#213963; color:#dbeafe; }}"
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


def sidebar_section_label_style() -> str:
    p = palette()
    meta = meta_font_pt()
    return f"font-size:{meta}px; color:{p['TEXT_DIM']}; padding:2px 4px;"


def git_changes_list_style() -> str:
    """Flat sidebar list (Git tab + Files tab changes)."""
    p = palette()
    sel = p["SELECTION"]
    return (
        f"QListWidget {{ background:{p['BG2']}; border:none; color:{p['TEXT']}; outline:none; }}"
        f"QListWidget::item {{ padding:2px 6px; border:none; outline:none; }}"
        f"QListWidget::item:hover {{ background:{p['BG3']}; }}"
        f"QListWidget::item:selected {{ background:{sel}; color:{p['SELECTION_TEXT']}; }}"
        f"QListWidget::item:selected:focus {{ background:{sel}; border:none; outline:none; }}"
        f"QListWidget::item:focus {{ border:none; outline:none; }}"
    )


def file_tree_sidebar_style() -> str:
    """File tree under the changes list — same item padding, no focus chrome."""
    p = palette()
    sel = p["SELECTION"]
    return (
        f"QTreeWidget#fileTree {{ background:{p['BG2']}; border:none; color:{p['TEXT']}; outline:none; }}"
        f"QTreeWidget#fileTree::item {{ padding:2px 6px; border:none; outline:none; border-radius:0; }}"
        f"QTreeWidget#fileTree::item:hover {{ background:{p['BG3']}; }}"
        f"QTreeWidget#fileTree::item:selected {{ background:{sel}; color:{p['SELECTION_TEXT']}; }}"
        f"QTreeWidget#fileTree::item:selected:focus {{ background:{sel}; border:none; outline:none; }}"
        f"QTreeWidget#fileTree::item:focus {{ border:none; outline:none; }}"
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
    fs = max(12, chat_font_pt() - 1)
    bg = "#151922" if current_theme() != "light" else p["INPUT_BG"]
    border = "#202a34" if current_theme() != "light" else p["BORDER_SUBTLE"]
    return (
        f"QLineEdit {{ background:{bg}; color:{p['TEXT']};"
        f"border:1px solid {border}; border-radius:8px;"
        f"margin:2px 14px 8px 14px; padding:7px 11px; font-size:{fs}px; }}"
        f"QLineEdit:focus {{ border:1px solid {ACCENT_DIM}; }}"
    )


def conversation_list_style() -> str:
    p = palette()
    sel = "#1d2d4d" if current_theme() != "light" else list_selection_bg()
    hover = "#171b24" if current_theme() != "light" else p["BG3"]
    return (
        f"QListWidget {{ background:{p['BG2']}; border:none; outline:none; }}"
        f"QListWidget::item {{ border:none; border-radius:7px; margin:1px 7px; }}"
        f"QListWidget::item:hover {{ background:{hover}; }}"
        f"QListWidget::item:selected {{ background:{sel}; color:{p['SELECTION_TEXT']}; }}"
    )


def flat_tab_style(object_name: str) -> str:
    p = palette()
    meta = meta_font_pt()
    prefix = f"QTabWidget#{object_name}"
    return (
        f"{prefix} {{ background:{p['BG2']}; border:0px; }}"
        f"{prefix}::pane {{ border:0px; background:{p['BG2']}; }}"
        f"{prefix} QTabBar {{ background:{p['BG2']}; border:0px; }}"
        f"{prefix} QTabBar::base {{ background:{p['BG2']};"
        "border:0px; height:0px; }"
        f"{prefix} QTabBar::tab {{ background:transparent; color:{p['TEXT_DIM']};"
        f"padding:8px 16px; border:0px; border-bottom:1px solid {p['BG2']};"
        f"font-size:{meta}px; font-weight:normal; margin:0px; }}"
        f"{prefix} QTabBar::tab:selected {{ color:{p['TEXT']};"
        f"border-bottom:1px solid {ACCENT};"
        "font-weight:bold; }"
        f"{prefix} QTabBar::tab:hover {{ color:{p['TEXT']};"
        f"background:{p['BG2']}; }}"
    )


def apply_flat_tab_style(tabs, object_name: str) -> None:
    tabs.setObjectName(object_name)
    tabs.setDocumentMode(True)
    tabs.tabBar().setDrawBase(False)
    tabs.setStyleSheet(flat_tab_style(object_name))


def sidebar_tab_style() -> str:
    return flat_tab_style("sidebarTabs")


def sidebar_settings_button_style() -> str:
    p = palette()
    return (
        f"QPushButton {{ background:{p['BG2']}; color:{p['TEXT_DIM']}; border:none;"
        f"border-top:1px solid {separator_color()}; font-size:15px; padding:6px; }}"
        f"QPushButton:hover {{ background:{p['BG3']}; color:{p['TEXT']}; }}"
    )


def timestamp_style() -> str:
    p = palette()
    return (
        f"color:{p['TEXT_DIM']}; font-size:{max(9, chat_font_pt() - 4)}px;"
        "background:transparent; padding:0 4px;"
    )


def crew_tone(
    crew_id: str = "",
    theme: str | None = None,
    custom_color: str = "",
) -> dict:
    theme_name = theme or current_theme()
    bg, border, accent = _CREW_TONES.get(str(crew_id or "").casefold(), {}).get(
        theme_name,
        _CREW_TONES["scout"].get(theme_name, _CREW_TONES["scout"]["dark"]),
    )
    if custom_color:
        border = custom_color
        accent = custom_color
    return {"background": bg, "border": border, "accent": accent}


def crew_name_style(crew_id: str = "", custom_color: str = "") -> str:
    tone = crew_tone(crew_id, custom_color=custom_color)
    return (
        f"color:{tone['accent']}; font-size:{max(10, chat_font_pt() - 3)}px;"
        "font-weight:600; background:transparent; padding:0 4px;"
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
QTabWidget::pane {{ border:0px; background:{p["BG2"]}; }}
QTabBar::tab {{
    background:transparent; color:{p["TEXT_DIM"]}; padding:8px 16px;
    border:0px; border-bottom:2px solid {p["BG2"]}; font-size:{meta}px;
    font-weight:normal; margin:0px;
}}
QTabBar::tab:selected {{ color:{p["TEXT"]}; border-bottom:2px solid {ACCENT}; font-weight:bold; }}
QTabBar::tab:hover {{ color:{p["TEXT"]}; background:{p["BG3"]}; border-radius:6px; }}
QTreeWidget      {{ background:{p["BG2"]}; border:none; font-size:{fs}px; color:{p["TEXT"]}; outline:none; }}
QTreeWidget::item {{ padding:3px 2px; border-radius:4px; border:none; outline:none; }}
QTreeWidget::item:hover {{ background:{p["BG3"]}; }}
QTreeWidget::item:selected {{ background:{sel}; color:{p["SELECTION_TEXT"]}; border:none; outline:none; }}
QTreeWidget::item:selected:focus {{ background:{sel}; color:{p["SELECTION_TEXT"]}; border:none; outline:none; }}
QTreeWidget::item:focus {{ border:none; outline:none; }}
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


def bubble_label_style(
    is_user: bool,
    font_pt: int | None = None,
    crew_id: str = "",
    crew_color: str = "",
) -> str:
    p = palette()
    fs = font_pt or chat_font_pt()
    if is_user:
        user_bg = "#3f73d8" if current_theme() != "light" else ACCENT_DIM
        return (
            f"background:{user_bg}; color:white; padding:8px 14px;"
            f"border-radius:16px; font-size:{fs}px; line-height:1.45;"
            f"font-family:{FONT_FAMILY};"
        )
    if crew_id:
        tone = crew_tone(crew_id, custom_color=crew_color)
        return (
            f"background:{tone['background']}; color:{p['BUBBLE_AI_TEXT']};"
            f"padding:10px 16px; border:1px solid {tone['border']};"
            f"border-left:4px solid {tone['accent']}; border-radius:18px;"
            f"font-size:{fs}px; line-height:1.45;"
        )
    return (
        f"background:transparent; color:{p['BUBBLE_AI_TEXT']}; padding:2px 4px;"
        f"border:none; border-radius:0; font-size:{fs}px;"
        f"line-height:1.55; font-family:{FONT_FAMILY};"
    )


def composer_style(font_pt: int | None = None) -> str:
    p = palette()
    fs = font_pt or chat_font_pt()
    return (
        f"QTextEdit {{ background:transparent; color:{p['INPUT_TEXT']}; border:none;"
        f"padding:4px 0; font-size:{fs}px; font-family:{FONT_FAMILY};"
        f"selection-background-color:{ACCENT}; placeholder-text-color:{p['TEXT_DIM']}; }}"
        f"QTextEdit:focus {{ border:none; }}"
    )


def composer_shell_style() -> str:
    p = palette()
    theme = current_theme()
    shell_bg = {
        "dark": "#12161d",
        "modern": "#121820",
    }.get(theme, p["INPUT_BG"])
    shell_border = {
        "dark": "#27313b",
        "modern": "#2c3742",
    }.get(theme, p["BORDER"])
    shell_focus = {
        "dark": "#385fba",
        "modern": "#3b6cc5",
    }.get(theme, ACCENT_DIM)
    return (
        f"QFrame#composerShell {{ background:{shell_bg};"
        f"border:1px solid {shell_border}; border-radius:9px; }}"
        f"QFrame#composerShell:focus-within {{ border:1px solid {shell_focus}; }}"
    )


def edit_bubble_style(font_pt: int | None = None) -> str:
    p = palette()
    fs = font_pt or chat_font_pt()
    return (
        f"background:{p['BG3']}; color:{p['TEXT']}; border:1px solid {ACCENT};"
        f"border-radius:10px; padding:8px 12px; font-size:{fs}px;"
    )


def apply_app_theme(app, theme: str | None = None) -> None:
    from ui.win_caption import install_caption_sync, sync_all_windows_captions

    theme_name = theme or current_theme()
    app.setFont(app_font())
    app.setStyleSheet(build_stylesheet(theme_name))
    install_caption_sync(app)
    sync_all_windows_captions(app, theme_name)


DARK_STYLE = build_stylesheet("dark")
