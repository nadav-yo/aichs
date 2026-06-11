from pathlib import Path
import sys

from storage.settings import SettingsStore

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

# Shared layout tokens — single source for fields, overlays, and modals.
FIELD_BORDER_RADIUS = 6
FIELD_PADDING = "8px 10px"
COMPACT_FIELD_BORDER_RADIUS = 6
OVERLAY_SEARCH_BORDER_RADIUS = 10
SEARCH_FIELD_BORDER_RADIUS = 8
MODAL_BORDER_RADIUS = 12

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


def _markdown_tokens(theme: str | None = None) -> dict[str, str]:
    theme_name = theme or current_theme()
    p = palette(theme_name)
    if theme_name == "light":
        return {
            "code_bg": "#eef3f8",
            "code_border": "#d6dee8",
            "code_fg": "#24292f",
            "pre_bg": "#f6f8fa",
            "quote_bg": "#f8fafc",
            "quote_border": "#d0d7de",
            "table_bg": p["BG2"],
            "table_header_bg": "#f6f8fa",
            "table_border": "#d0d7de",
            "file_bg": "#e8f5f0",
            "file_border": "#b7e2d8",
            "file_fg": "#096053",
        }
    if theme_name == "modern":
        return {
            "code_bg": "#1b2f34",
            "code_border": "#2d5057",
            "code_fg": "#dbfbff",
            "pre_bg": "#151b1f",
            "quote_bg": "#192123",
            "quote_border": "#506462",
            "table_bg": p["BG2"],
            "table_header_bg": "#1b2226",
            "table_border": "#303a40",
            "file_bg": "#14322f",
            "file_border": "#276a60",
            "file_fg": "#c5f5ee",
        }
    return {
        "code_bg": "#20283a",
        "code_border": "#33415f",
        "code_fg": "#eef4ff",
        "pre_bg": "#141820",
        "quote_bg": "#181d24",
        "quote_border": "#4b5a6b",
        "table_bg": p["BG2"],
        "table_header_bg": "#1c2129",
        "table_border": "#303844",
        "file_bg": "#162b3d",
        "file_border": "#294a68",
        "file_fg": "#b9ddff",
    }


def markdown_css(font_pt: int | None = None, theme: str | None = None) -> str:
    p = palette(theme)
    theme_name = theme or current_theme()
    t = _markdown_tokens(theme_name)
    fs = font_pt or chat_font_pt()
    link = p["LINK"]
    file_link = p["FILE_LINK"]
    return (
        f"body {{ margin:0; padding:0; color:{p['BUBBLE_AI_TEXT']}; font-size:{fs}px;"
        f"line-height:1.58; font-family:{FONT_FAMILY}; }}"
        f"a {{ color:{link}; text-decoration:none; }}"
        f"a.aichs-file-link {{ color:{file_link}; text-decoration:none; }}"
        f"code {{ background:{t['code_bg']}; background-color:{t['code_bg']};"
        f"border:1px solid {t['code_border']}; border-radius:4px;"
        f"padding:1px 4px; color:{t['code_fg']}; font-family:{MONO_FONT_CSS};"
        f"font-size:{max(10, fs - 1)}px; }}"
        f"pre {{ background:{t['pre_bg']}; background-color:{t['pre_bg']};"
        "border-radius:8px; padding:10px 12px;"
        "white-space:pre-wrap; margin:12px 8px 14px 8px; line-height:1.45; }"
        f"pre code, pre code span {{ background:{t['pre_bg']}; background-color:{t['pre_bg']};"
        f"border:0; color:{t['code_fg']}; padding:0; border-radius:0; }}"
        f"h1 {{ margin:14px 0 8px 0; font-size:{max(fs + 8, 22)}px; font-weight:700; }}"
        f"h2 {{ margin:14px 0 7px 0; font-size:{max(fs + 5, 19)}px; font-weight:700; }}"
        f"h3 {{ margin:12px 0 6px 0; font-size:{max(fs + 2, 16)}px; font-weight:650; }}"
        "ul,ol { margin:8px 0 10px 0; padding-left:24px; }"
        "li { margin:4px 0; padding-left:2px; }"
        "p { margin:6px 0; }"
        f"blockquote {{ background:{t['quote_bg']}; background-color:{t['quote_bg']};"
        f"margin:10px 4px 12px 4px; padding:8px 12px;"
        f"border-left:3px solid {t['quote_border']}; border-radius:6px;"
        f"color:{p['TEXT_DIM']}; }}"
        f"table {{ background:{t['table_bg']}; border-collapse:collapse; margin:12px 0;"
        "border-spacing:0; }"
        f"th,td {{ border:1px solid {t['table_border']}; padding:6px 9px; }}"
        f"th {{ background:{t['table_header_bg']}; background-color:{t['table_header_bg']};"
        f"color:{p['TEXT']}; font-weight:650; }}"
        f"hr {{ border:0; border-top:1px solid {t['table_border']}; margin:16px 0; }}"
    )


def markdown_code_block_styles(font_pt: int | None = None, theme: str | None = None) -> dict[str, str]:
    theme_name = theme or current_theme()
    t = _markdown_tokens(theme_name)
    fs = font_pt or chat_font_pt()
    code_size = max(10, fs - 1)
    return {
        "table": "margin:12px 8px 14px 8px;",
        "header": (
            f"background:{t['pre_bg']}; background-color:{t['pre_bg']};"
            "padding:8px 10px 0 10px; text-align:right;"
        ),
        "copy": (
            f"color:{t['code_fg']}; background:{t['pre_bg']};"
            f"background-color:{t['pre_bg']}; border:1px solid {t['code_border']};"
            "text-decoration:none; border-radius:4px; padding:1px 6px;"
            f"font-family:{MONO_FONT_CSS}; font-weight:600;"
        ),
        "cell": f"background:{t['pre_bg']}; background-color:{t['pre_bg']}; padding:10px 12px;",
        "pre": (
            f"margin:0; padding:0; background:{t['pre_bg']};"
            f"background-color:{t['pre_bg']}; white-space:pre-wrap; line-height:1.45;"
        ),
        "text": (
            f"font-family:{MONO_FONT_CSS}; font-size:{code_size}px;"
            f"color:{t['code_fg']}; background:{t['pre_bg']};"
            f"background-color:{t['pre_bg']};"
        ),
    }


def markdown_file_link_style(theme: str | None = None) -> str:
    t = _markdown_tokens(theme)
    return (
        f"color:{t['file_fg']}; background:{t['file_bg']};"
        f"border:1px solid {t['file_border']};"
        "text-decoration:none; border-radius:5px; padding:1px 5px;"
        "font-weight:600;"
    )


def inline_code_style(theme: str | None = None) -> str:
    t = _markdown_tokens(theme)
    return (
        f"color:{t['code_fg']}; background:{t['code_bg']};"
        f"background-color:{t['code_bg']}; border:1px solid {t['code_border']};"
        "text-decoration:none; border-radius:4px; padding:1px 4px;"
        f"font-family:{MONO_FONT_CSS}; font-weight:600;"
    )


def _state_selector(selector: str, state: str) -> str:
    parts = [part.strip() for part in selector.split(",") if part.strip()]
    return ", ".join(f"{part}:{state}" for part in parts) or f"{selector}:{state}"


def code_text_edit_style(
    *,
    selector: str = "QPlainTextEdit",
    font_pt: int | None = None,
    padding: str = "8px",
    theme: str | None = None,
) -> str:
    theme_name = theme or current_theme()
    p = palette(theme_name)
    t = _markdown_tokens(theme_name)
    fs = font_pt or mono_font_pt()
    return (
        f"{selector} {{ background:{t['pre_bg']}; color:{t['code_fg']};"
        f"border:1px solid {t['code_border']}; border-radius:6px;"
        f"font-family:{MONO_FONT_CSS}; font-size:{fs}px; padding:{padding};"
        f"selection-background-color:{p['SELECTION']};"
        f"selection-color:{p['SELECTION_TEXT']}; }}"
    )


def combo_box_field_style(
    *,
    selector: str = "QComboBox",
    font_pt: int | None = None,
    padding_v: str = "6px",
    padding_h: str = "10px",
    border_radius: int = FIELD_BORDER_RADIUS,
    drop_down_width: int = 24,
    background: str | None = None,
    border_color: str | None = None,
    min_height: int | None = None,
    theme: str | None = None,
) -> str:
    theme_name = theme or current_theme()
    p = palette(theme_name)
    fs = font_pt or 13
    bg = background or p["BG3"]
    border = border_color or p["BORDER"]
    row_height = min_height or max(30, fs + 14)
    return (
        f"{selector} {{ background:{bg}; color:{p['TEXT']};"
        f"border:1px solid {border}; border-radius:{border_radius}px;"
        f"padding:{padding_v} {padding_h}; padding-right:{drop_down_width}px;"
        f"font-size:{fs}px; min-height:{row_height}px; }}"
        f"{selector}:hover {{ border:1px solid {p['TEXT_DIM']}; }}"
        f"{_state_selector(selector, 'focus')} {{ border:1px solid {ACCENT}; }}"
        f"{selector}::drop-down {{ border:none; width:{drop_down_width}px;"
        f"subcontrol-origin: padding; subcontrol-position: top right; }}"
    )


def form_field_style(
    *,
    selector: str = "QLineEdit, QTextEdit, QSpinBox, QDoubleSpinBox",
    font_pt: int | None = None,
    padding: str = FIELD_PADDING,
    border_radius: int = FIELD_BORDER_RADIUS,
    include_combo_popup: bool = True,
    theme: str | None = None,
) -> str:
    theme_name = theme or current_theme()
    p = palette(theme_name)
    fs = font_pt or 13
    base = (
        f"{selector} {{ background:{p['BG3']}; color:{p['TEXT']};"
        f"border:1px solid {p['BORDER']}; border-radius:{border_radius}px;"
        f"padding:{padding}; font-size:{fs}px; }}"
        f"{_state_selector(selector, 'focus')} {{ border:1px solid {ACCENT}; }}"
    )
    combo = combo_box_field_style(
        theme=theme_name,
        font_pt=fs,
        padding_v="6px",
        padding_h="10px",
        border_radius=border_radius,
    )
    if not include_combo_popup:
        return base + combo
    return (
        base
        + combo
        + combo_box_popup_style(theme_name, bg=p["BG3"], border_radius=border_radius, font_pt=fs)
    )


def compact_field_style(
    *,
    selector: str = "QLineEdit",
    font_pt: int | None = None,
    padding: str = "4px 8px",
    border_radius: int = COMPACT_FIELD_BORDER_RADIUS,
    border_color: str | None = None,
    theme: str | None = None,
) -> str:
    theme_name = theme or current_theme()
    p = palette(theme_name)
    fs = font_pt or meta_font_pt()
    border = border_color or p["BORDER"]
    return (
        f"{selector} {{ background:{p['BG3']}; color:{p['TEXT']};"
        f"border:1px solid {border}; border-radius:{border_radius}px;"
        f"padding:{padding}; font-size:{fs}px; }}"
        f"{_state_selector(selector, 'focus')} {{ border:1px solid {ACCENT}; }}"
    )


def editor_text_area_style(
    *,
    selector: str = "QPlainTextEdit",
    bg: str | None = None,
    text_color: str | None = None,
    border: str = "none",
    border_radius: int = 0,
    padding: str = "0",
    font_pt: int | None = None,
    theme: str | None = None,
) -> str:
    p = palette(theme)
    fs = font_pt or mono_font_pt()
    surface = bg or p["BG3"]
    fg = text_color or p["TEXT"]
    return (
        f"{selector} {{ background:{surface}; color:{fg}; border:{border};"
        f"border-radius:{border_radius}px; padding:{padding};"
        f"font-family:{MONO_FONT_CSS}; font-size:{fs}px;"
        f"selection-background-color:{ACCENT}; selection-color:{p['SELECTION_TEXT']}; }}"
    )


def compact_combo_box_style(
    *,
    selector: str = "QComboBox",
    font_pt: int | None = None,
    padding: str = "4px 8px",
    border_radius: int = COMPACT_FIELD_BORDER_RADIUS,
    drop_down_width: int = 20,
    background: str | None = None,
    border_color: str | None = None,
    popup_background: str | None = None,
    popup_item_padding: str = "6px 10px",
    theme: str | None = None,
) -> str:
    theme_name = theme or current_theme()
    p = palette(theme_name)
    fs = font_pt or meta_font_pt()
    popup_bg = popup_background or p["BG3"]
    pad_parts = padding.split()
    padding_v = pad_parts[0] if pad_parts else "4px"
    padding_h = pad_parts[1] if len(pad_parts) > 1 else padding_v
    return (
        combo_box_field_style(
            selector=selector,
            font_pt=fs,
            padding_v=padding_v,
            padding_h=padding_h,
            border_radius=border_radius,
            drop_down_width=drop_down_width,
            background=background,
            border_color=border_color,
            theme=theme_name,
        )
        + combo_box_popup_style(
            theme_name,
            bg=popup_bg,
            border_radius=border_radius,
            font_pt=fs,
            item_padding=popup_item_padding,
        )
    )


def code_surface_colors(theme: str | None = None) -> dict[str, str]:
    t = _markdown_tokens(theme)
    return {
        "background": t["pre_bg"],
        "foreground": t["code_fg"],
        "border": t["code_border"],
    }


def search_match_style(theme: str | None = None) -> str:
    p = palette(theme)
    return (
        f"color:{p['SELECTION_TEXT']}; background:{p['SELECTION']};"
        f"background-color:{p['SELECTION']}; border-radius:3px;"
        "font-weight:700; padding:0 2px;"
    )


def user_reference_style(theme: str | None = None) -> str:
    theme_name = theme or current_theme()
    if theme_name == "light":
        fg, bg, border = "#0f3f94", "#e8f0ff", "#bfd2ff"
    elif theme_name == "modern":
        fg, bg, border = "#b9e4ff", "#172b3a", "#2f607d"
    else:
        fg, bg, border = "#dbeafe", "#182847", "#2c4e86"
    return (
        f"color:{fg}; background:{bg}; border:1px solid {border};"
        "text-decoration:none; border-radius:5px; padding:1px 5px;"
        "font-weight:600;"
    )


def composer_reference_colors() -> dict:
    if current_theme() == "light":
        return {"fg": "#0f3f94", "bg": "#e8f0ff"}
    return {"fg": "#dbeafe", "bg": "#1c3154"}


def surface_frame_style(
    *,
    selector: str = "QFrame",
    bg: str | None = None,
    border: str | None = None,
    border_radius: int = 8,
    theme: str | None = None,
) -> str:
    p = palette(theme)
    surface = bg or p["BG3"]
    line = border or p["BORDER"]
    return (
        f"{selector} {{ background:{surface}; border:1px solid {line};"
        f"border-radius:{border_radius}px; }}"
    )


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
    return surface_frame_style(bg=bg, border=border, border_radius=8)


def dialog_shell_style(
    *,
    selector: str = "QDialog",
    include_labels: bool = False,
    theme: str | None = None,
) -> str:
    p = palette(theme)
    label_style = (
        f"QLabel {{ color:{p['TEXT']}; background:transparent; }}"
        if include_labels
        else ""
    )
    return (
        f"{selector} {{ background:{p['BG2']}; color:{p['TEXT']}; }}"
        f"{label_style}"
    )


def panel_stack_style(*, theme: str | None = None) -> str:
    """Background fill for a QStackedWidget (applied directly on the widget)."""
    p = palette(theme)
    return f"background:{p['BG2']};"


def avatar_preview_style(
    *,
    border_color: str | None = None,
    border_width: int = 1,
    size: int = 48,
    theme: str | None = None,
) -> str:
    p = palette(theme)
    color = border_color or p["BORDER"]
    radius = size // 2
    return f"border:{border_width}px solid {color}; border-radius:{radius}px;"


def transparent_scroll_area_style(
    *,
    selector: str = "QScrollArea",
    bg: str | None = None,
    border: str = "none",
    include_viewport: bool = True,
    theme: str | None = None,
) -> str:
    p = palette(theme)
    surface = bg or p["BG2"]
    viewport = (
        f"{selector} QWidget {{ background:{surface}; }}"
        if include_viewport
        else ""
    )
    return (
        f"{selector} {{ background:{surface}; border:{border}; }}"
        f"{viewport}"
    )


def menu_style(
    *,
    selector: str = "QMenu",
    item_padding: str = "6px 24px 6px 12px",
    border_radius: int = 8,
    item_radius: int = 4,
    theme: str | None = None,
) -> str:
    p = palette(theme)
    return (
        f"{selector} {{ background-color:{p['BG3']}; color:{p['TEXT']};"
        f"border:1px solid {p['BORDER']}; border-radius:{border_radius}px;"
        "padding:4px; }"
        f"{selector}::item {{ padding:{item_padding}; border-radius:{item_radius}px; }}"
        f"{selector}::item:selected {{ background-color:{p['SELECTION']};"
        f"color:{p['SELECTION_TEXT']}; }}"
        f"{selector}::item:disabled {{ color:{p['TEXT_DIM']}; }}"
        f"{selector}::separator {{ height:1px; background:{p['BORDER_SUBTLE']};"
        "margin:4px 6px; }"
    )


def dialog_button_box_style(
    *,
    selector: str = "QDialogButtonBox",
    min_button_width: int = 76,
    theme: str | None = None,
) -> str:
    p = palette(theme)
    return (
        f"{selector} {{ background-color:transparent; border:none; }}"
        f"{selector} QPushButton {{ min-width:{min_button_width}px;"
        "padding:6px 14px; }"
        f"{selector} QPushButton:disabled {{ color:{p['TEXT_DIM']};"
        f"background:{p['BG2']}; border-color:{p['BORDER_SUBTLE']}; }}"
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


def separator_frame_style(
    *,
    selector: str = "QFrame",
    color: str | None = None,
    max_height: int = 1,
) -> str:
    sep = color or separator_color()
    return f"{selector} {{ background:{sep}; color:{sep}; border:none; max-height:{max_height}px; }}"


def center_notice_style() -> str:
    return hint_label_style(padding="8px")


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


def primary_button_style(
    *,
    selector: str = "QPushButton",
    border_radius: int = 6,
    padding: str = "6px 18px",
    font_size: int | None = None,
    font_weight: str = "700",
) -> str:
    fs = f"font-size:{font_size}px;" if font_size is not None else ""
    return (
        f"{selector} {{ background:{ACCENT}; color:white; border:none;"
        f"border-radius:{border_radius}px; padding:{padding}; {fs}"
        f"font-weight:{font_weight}; }}"
        f"{selector}:hover {{ background:{ACCENT_HOVER}; }}"
        f"{selector}:pressed {{ background:{ACCENT_DIM}; }}"
        f"{selector}:disabled {{ background:{palette()['BG3']}; color:{palette()['TEXT_DIM']};"
        f"border:1px solid {palette()['BORDER']}; }}"
    )


def secondary_button_style(
    *,
    selector: str = "QPushButton",
    border_radius: int = 6,
    padding: str = "4px 12px",
    margin: str | None = None,
    font_size: int | None = None,
    font_weight: str = "400",
    text_color: str | None = None,
    background: str | None = None,
    border_color: str | None = None,
) -> str:
    p = palette()
    fs = f"font-size:{font_size}px;" if font_size is not None else ""
    margin_rule = f"margin:{margin};" if margin is not None else ""
    fg = text_color or p["TEXT"]
    bg = background or p["BG3"]
    border = border_color or p["BORDER"]
    return (
        f"{selector} {{ background:{bg}; color:{fg};"
        f"border:1px solid {border}; border-radius:{border_radius}px;"
        f"padding:{padding}; {margin_rule}{fs}font-weight:{font_weight}; }}"
        f"{selector}:hover {{ color:{p['TEXT']}; background:{p['BORDER']}; }}"
        f"{selector}:pressed {{ background:{p['BG2']}; }}"
        f"{selector}:disabled {{ color:{p['TEXT_DIM']}; background:{p['BG2']};"
        f"border-color:{p['BORDER_SUBTLE']}; }}"
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


def new_chat_button_style(theme: str | None = None) -> str:
    theme_name = theme or current_theme()
    p = palette(theme_name)
    fs = max(12, chat_font_pt() - 1)
    soft = ACCENT_SOFT_LIGHT if theme_name == "light" else ACCENT_SOFT_DARK
    hover_bg = p["SELECTION"]
    hover_fg = ACCENT_DIM if theme_name == "light" else "#dbeafe"
    return (
        f"QPushButton {{ background:{soft}; color:{ACCENT}; border:none;"
        f"border-radius:8px; margin:8px 14px 6px 14px; padding:7px 12px;"
        f"font-size:{fs}px; font-weight:600; }}"
        f"QPushButton:hover {{ background:{hover_bg}; color:{hover_fg}; }}"
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


def bordered_icon_button_style(
    *,
    selector: str = "QToolButton",
    size_px: int | None = None,
    border_radius: int = 6,
    padding: str = "3px",
    text_color: str | None = None,
    background: str | None = None,
    border_color: str | None = None,
) -> str:
    p = palette()
    fg = text_color or p["TEXT_DIM"]
    bg = background or p["BG3"]
    border = border_color or p["BORDER"]
    size = ""
    if size_px is not None:
        size = (
            f"min-width:{size_px}px; max-width:{size_px}px;"
            f"min-height:{size_px}px; max-height:{size_px}px;"
        )
    return (
        f"{selector} {{ background:{bg}; color:{fg};"
        f"border:1px solid {border}; border-radius:{border_radius}px;"
        f"padding:{padding}; {size}}}"
        f"{selector}:hover {{ color:{p['TEXT']}; background:{p['BORDER']}; }}"
        f"{selector}:pressed {{ background:{p['BG2']}; }}"
        f"{selector}:disabled {{ color:{p['TEXT_DIM']}; background:{p['BG2']};"
        f"border-color:{p['BORDER_SUBTLE']}; }}"
    )


def rail_button_style(
    *,
    font_size: int,
    active: bool = False,
    theme: str | None = None,
) -> str:
    """Activity rail navigation (Files, Git, …)."""
    p = palette(theme)
    bg = p["SELECTION"] if active else "transparent"
    fg = p["SELECTION_TEXT"] if active else p["TEXT_DIM"]
    return (
        f"QPushButton {{ background-color:{bg}; color:{fg}; border:0px;"
        "border-radius:7px; padding:7px 2px;"
        f"font-size:{font_size}px; font-weight:600; }}"
        f"QPushButton:hover {{ background-color:{p['BG3']}; color:{p['TEXT']}; }}"
    )


def git_action_button_style(
    accent_color: str = ACCENT,
    theme: str | None = None,
) -> str:
    """Compact accent-outlined git toolbar action."""
    p = palette(theme)
    return (
        f"QPushButton {{ background:{p['BG2']}; color:{accent_color};"
        f"border:1px solid {accent_color}; border-radius:{FIELD_BORDER_RADIUS}px;"
        "padding:0 6px; min-width:16px; min-height:22px; }"
        f"QPushButton:hover {{ background:{p['BG3']}; color:{p['TEXT']};"
        f"border-color:{accent_color}; }}"
        f"QPushButton:pressed {{ background:{p['BORDER']}; }}"
        f"QPushButton:disabled {{ background:{p['BG2']}; color:{p['TEXT_DIM']};"
        f"border-color:{p['BORDER_SUBTLE']}; }}"
    )


def git_change_button_style(theme: str | None = None) -> str:
    """Neutral compact button in git change rows."""
    p = palette(theme)
    return (
        f"QPushButton {{ background:{p['BG2']}; color:{p['TEXT']};"
        f"border:1px solid {p['BORDER_SUBTLE']}; border-radius:{FIELD_BORDER_RADIUS}px;"
        "padding:3px 8px; min-height:22px; }"
        f"QPushButton:hover {{ background:{p['BG3']}; border-color:{p['BORDER']}; }}"
        f"QPushButton:pressed {{ background:{p['BORDER']}; }}"
        f"QPushButton:disabled {{ background:{p['BG2']}; color:{p['TEXT_DIM']};"
        f"border-color:{p['BORDER_SUBTLE']}; }}"
    )


def context_panel_title_button_style(
    *,
    selector: str = "QPushButton#contextPanelTitleButton",
    theme: str | None = None,
) -> str:
    p = palette(theme)
    return (
        f"{selector} {{ background:{p['BG2']}; color:{p['TEXT']};"
        f"border:1px solid {p['BG2']}; border-radius:{FIELD_BORDER_RADIUS}px;"
        "padding:4px 2px; text-align:left;"
        f"font-size:{max(13, chat_font_pt())}px; font-weight:700; }}"
        f"{selector}:hover {{ background:{p['BG3']}; border-color:{p['BORDER_SUBTLE']}; }}"
    )


def toggle_tab_button_style(
    *,
    selector: str = "QPushButton",
    theme: str | None = None,
) -> str:
    """Checkable icon tab (run log / language context)."""
    p = palette(theme)
    return (
        f"{selector} {{ background:transparent; border:none; padding:0;"
        f"border-radius:{FIELD_BORDER_RADIUS}px; }}"
        f"{selector}:hover {{ background:{p['BG3']}; }}"
        f"{selector}:checked {{ background:{p['SELECTION']}; }}"
    )


def skill_chip_style(
    *,
    selector: str = "QPushButton",
    theme: str | None = None,
) -> str:
    p = palette(theme)
    return (
        f"{selector} {{ background-color:{p['BG3']}; color:{p['TEXT']};"
        f"border:1px solid {p['BORDER']}; border-radius:8px;"
        "font-size:11px; padding-left:8px; padding-right:8px; }"
        f"{selector}:hover {{ color:{ACCENT}; border-color:{ACCENT}; }}"
    )


def attachment_thumbnail_style(
    *,
    selector: str = "QLabel",
    theme: str | None = None,
) -> str:
    p = palette(theme)
    return f"{selector} {{ border:1px solid {p['BORDER']}; border-radius:{FIELD_BORDER_RADIUS}px; }}"


def attachment_remove_button_style(
    *,
    selector: str = "QPushButton",
    theme: str | None = None,
) -> str:
    p = palette(theme)
    return (
        f"{selector} {{ background:{p['BG3']}; color:{p['TEXT_DIM']};"
        f"border:1px solid {p['BORDER']}; border-radius:9px; font-size:10px; padding:0; }}"
        f"{selector}:hover {{ color:#ff5555; }}"
    )


def conversation_row_title_style(*, theme: str | None = None) -> str:
    p = palette(theme)
    fs = max(12, chat_font_pt() - 1)
    return (
        f"font-size:{fs}px; color:{p['TEXT']};"
        "background:transparent; font-weight:500;"
    )


def conversation_row_inline_edit_style(*, theme: str | None = None) -> str:
    p = palette(theme)
    fs = chat_font_pt()
    return (
        f"font-size:{fs}px; color:{p['TEXT']}; background:{p['BG3']};"
        f"border:1px solid {p['BORDER']}; padding:1px 4px;"
    )


def conversation_row_icon_label_style(
    *,
    color: str | None = None,
    hover_color: str | None = None,
    font_pt: int | None = None,
    theme: str | None = None,
) -> str:
    p = palette(theme)
    fg = color or p["TEXT_DIM"]
    fs = font_pt if font_pt is not None else max(10, meta_font_pt())
    rules = (
        f"QLabel {{ color:{fg}; background:transparent; font-size:{fs}px; }}"
    )
    if hover_color:
        rules += f"QLabel:hover {{ color:{hover_color}; }}"
    return rules


def conversation_row_restore_button_style(*, theme: str | None = None) -> str:
    return secondary_button_style(
        padding="2px 8px",
        font_size=max(10, meta_font_pt()),
        border_radius=FIELD_BORDER_RADIUS,
    )


def conversation_trash_header_style(*, theme: str | None = None) -> str:
    p = palette(theme)
    return (
        f"TrashHeader {{ background:{p['BG2']}; border-top:1px solid {p['BORDER']}; }}"
        f"TrashHeader:hover {{ background:{p['BG3']}; }}"
    )


def checkbox_style(
    *,
    font_pt: int | None = None,
    font_weight: str = "400",
    indicator_px: int = 14,
    spacing_px: int = 6,
    text_color: str = "",
    checked_image: str | None = None,
) -> str:
    p = palette()
    fs = font_pt or meta_font_pt()
    fg = text_color or p["TEXT"]
    if checked_image is None:
        checked_image = (
            Path(__file__).resolve().parents[1] / "assets" / "checkmark.svg"
        ).as_posix()
    image_rule = f'image: url("{checked_image}");' if checked_image else ""
    return (
        "QCheckBox {"
        f" color: {fg};"
        f" font-size: {fs}px;"
        f" font-weight: {font_weight};"
        f" spacing: {spacing_px}px;"
        " background-color: transparent;"
        " border: none;"
        " padding: 0px;"
        "}"
        "QCheckBox::indicator {"
        f" width: {indicator_px}px;"
        f" height: {indicator_px}px;"
        f" background-color: {p['BG3']};"
        f" border: 1px solid {p['BORDER']};"
        " border-radius: 3px;"
        "}"
        "QCheckBox::indicator:hover {"
        f" border: 1px solid {ACCENT};"
        "}"
        "QCheckBox::indicator:checked {"
        f" background-color: {p['BG3']};"
        f" border: 1px solid {ACCENT};"
        f" {image_rule}"
        "}"
        "QCheckBox::indicator:unchecked {"
        f" background-color: {p['BG3']};"
        "}"
        "QCheckBox::indicator:disabled {"
        f" background-color: {p['BG2']};"
        f" border: 1px solid {p['BORDER_SUBTLE']};"
        "}"
    )


def sidebar_section_label_style() -> str:
    return hint_label_style(padding="2px 4px")


def title_label_style(
    *,
    selector: str = "QLabel",
    font_pt: int | None = None,
    font_weight: str = "650",
    text_color: str | None = None,
    background: str = "transparent",
    padding: str = "0",
    theme: str | None = None,
) -> str:
    p = palette(theme)
    size = font_pt if font_pt is not None else chat_font_pt() + 2
    color = text_color or p["TEXT"]
    return (
        f"{selector} {{ color:{color}; background:{background};"
        f"font-size:{size}px; font-weight:{font_weight}; padding:{padding}; }}"
    )


def section_label_style(
    *,
    selector: str = "QLabel",
    font_pt: int | None = None,
    font_weight: str = "600",
    text_color: str | None = None,
    background: str = "transparent",
    padding: str = "0",
    theme: str | None = None,
) -> str:
    return hint_label_style(
        selector=selector,
        font_pt=font_pt,
        font_weight=font_weight,
        text_color=text_color,
        background=background,
        padding=padding,
        theme=theme,
    )


def hint_label_style(
    *,
    selector: str = "QLabel",
    font_pt: int | None = None,
    font_weight: str = "normal",
    text_color: str | None = None,
    background: str = "transparent",
    padding: str = "0",
    font_family: str | None = None,
    theme: str | None = None,
) -> str:
    """Secondary / caption text (hints, timestamps, status lines)."""
    p = palette(theme)
    size = font_pt if font_pt is not None else meta_font_pt()
    color = text_color or p["TEXT_DIM"]
    family = f"font-family:{font_family};" if font_family else ""
    return (
        f"{selector} {{ color:{color}; background:{background};"
        f"font-size:{size}px; font-weight:{font_weight}; padding:{padding}; {family}}}"
    )


def field_label_style(
    *,
    selector: str = "QLabel",
    font_pt: int | None = None,
    text_color: str | None = None,
    background: str = "transparent",
    padding: str = "0",
    theme: str | None = None,
) -> str:
    """Form field labels above inputs."""
    return hint_label_style(
        selector=selector,
        font_pt=font_pt,
        font_weight="500",
        text_color=text_color,
        background=background,
        padding=padding,
        theme=theme,
    )


def status_pill_style(
    *,
    selector: str = "QLabel",
    tone: str = "neutral",
    font_pt: int | None = None,
    padding: str = "4px 8px",
    border_radius: int = 6,
    text_color: str | None = None,
    background: str | None = None,
    border_color: str | None = None,
    theme: str | None = None,
) -> str:
    p = palette(theme)
    tones = {
        "accent": (ACCENT, p["BG3"], p["BORDER"]),
        "danger": ("#f87171", "#35191d", "#5f252d"),
        "disabled": (p["TEXT_DIM"], p["BG3"], p["BORDER"]),
        "success": (p["SUCCESS"], p["SUCCESS_BG"], p["SUCCESS_BORDER"]),
        "neutral": (p["TEXT_DIM"], p["BG3"], p["BORDER_SUBTLE"]),
    }
    fg, bg, border = tones.get(tone, tones["neutral"])
    size = font_pt if font_pt is not None else meta_font_pt()
    return (
        f"{selector} {{ color:{text_color or fg}; background:{background or bg};"
        f"border:1px solid {border_color or border}; border-radius:{border_radius}px;"
        f"padding:{padding}; font-size:{size}px; }}"
    )


# ---------------------------------------------------------------------------
# QListWidget variants (pick one — do not invent ad-hoc list QSS in widgets):
#   flat / git_changes_list_style   — compact sidebar rows, filled selection
#   conversation_list_style         — rounded rows, conversation sidebar
#   overlay_results_list_style      — modal results, accent left bar
#   navigation_list_style           — section nav, dim items + accent bar
#   contained_list_style            — bordered in-panel list
#   popover_list_style              — transient picker, accent fill selection
# ---------------------------------------------------------------------------


def _flat_list_style(
    *,
    selector: str = "QListWidget",
    item_padding: str | None = None,
    item_radius: int = 0,
    item_margin: str = "0",
    item_border: str = "none",
    hover_bg: str | None = None,
    selected_bg: str | None = None,
    selected_text: str | None = None,
    selected_border_left: str | None = None,
    include_focus_reset: bool = True,
    theme: str | None = None,
) -> str:
    p = palette(theme)
    sel_bg = selected_bg or p["SELECTION"]
    sel_text = selected_text or p["SELECTION_TEXT"]
    hover = hover_bg or p["BG3"]
    item_decl = ["border:none", "outline:none"]
    if item_padding:
        item_decl.append(f"padding:{item_padding}")
    if item_radius:
        item_decl.append(f"border-radius:{item_radius}px")
    if item_margin and item_margin != "0":
        item_decl.append(f"margin:{item_margin}")
    if item_border != "none":
        item_decl.append(f"border:{item_border}")
    sel_decl = [f"background:{sel_bg}"]
    if selected_text is not None:
        sel_decl.append(f"color:{sel_text}")
    elif selected_border_left is None:
        sel_decl.append(f"color:{sel_text}")
    if selected_border_left:
        sel_decl.append(f"border-left:{selected_border_left}")
    style = (
        f"{selector} {{ background:{p['BG2']}; border:none; color:{p['TEXT']}; outline:none; }}"
        f"{selector}::item {{ {'; '.join(item_decl)}; }}"
        f"{selector}::item:hover {{ background:{hover}; }}"
        f"{selector}::item:selected {{ {'; '.join(sel_decl)}; }}"
    )
    if include_focus_reset:
        style += (
            f"{selector}::item:selected:focus {{ background:{sel_bg}; border:none; outline:none; }}"
            f"{selector}::item:focus {{ border:none; outline:none; }}"
        )
    return style


def git_changes_list_style() -> str:
    """Flat sidebar list — compact rows, filled selection (git/file changes)."""
    return _flat_list_style(item_padding="2px 6px")


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


def contained_list_style(
    *,
    selector: str = "QListWidget",
    item_padding: str = "6px 8px",
    item_radius: int = 5,
    item_margin: str = "1px 4px",
    border_radius: int = 7,
    bg: str | None = None,
    border: str | None = None,
    theme: str | None = None,
) -> str:
    """Bordered in-panel list (settings sections, docs nav, recent files)."""
    p = palette(theme)
    surface = bg or p["BG2"]
    line = border or p["BORDER_SUBTLE"]
    selectors = [part.strip() for part in selector.split(",") if part.strip()]
    base_selector = ", ".join(selectors) or selector
    item_selector = ", ".join(f"{part}::item" for part in selectors) or f"{selector}::item"
    item_hover_selector = ", ".join(f"{part}::item:hover" for part in selectors) or f"{selector}::item:hover"
    item_selected_selector = (
        ", ".join(f"{part}::item:selected" for part in selectors)
        or f"{selector}::item:selected"
    )
    item_selected_focus_selector = (
        ", ".join(f"{part}::item:selected:focus" for part in selectors)
        or f"{selector}::item:selected:focus"
    )
    item_focus_selector = ", ".join(f"{part}::item:focus" for part in selectors) or f"{selector}::item:focus"
    return (
        f"{base_selector} {{ background:{surface}; color:{p['TEXT']};"
        f"border:1px solid {line}; border-radius:{border_radius}px;"
        "outline:none; padding:4px; }"
        f"{item_selector} {{ padding:{item_padding}; border:none;"
        f"border-radius:{item_radius}px; margin:{item_margin}; }}"
        f"{item_hover_selector} {{ background:{p['BG3']}; }}"
        f"{item_selected_selector} {{ background:{p['SELECTION']};"
        f"color:{p['SELECTION_TEXT']}; }}"
        f"{item_selected_focus_selector} {{ background:{p['SELECTION']};"
        f"color:{p['SELECTION_TEXT']}; border:none; outline:none; }}"
        f"{item_focus_selector} {{ border:none; outline:none; }}"
        f"{base_selector}:disabled {{ color:{p['TEXT_DIM']}; }}"
    )


def contained_tree_style(
    *,
    selector: str = "QTreeWidget",
    item_padding: str = "5px",
    item_radius: int = 4,
    border_radius: int = 6,
    bg: str | None = None,
    border: str | None = None,
    header_selector: str = "QHeaderView::section",
    theme: str | None = None,
) -> str:
    p = palette(theme)
    surface = bg or p["BG3"]
    line = border or p["BORDER"]
    return (
        f"{selector} {{ background:{surface}; color:{p['TEXT']};"
        f"border:1px solid {line}; border-radius:{border_radius}px; outline:none; }}"
        f"{selector}::item {{ padding:{item_padding}; border-radius:{item_radius}px;"
        "border:none; outline:none; }"
        f"{selector}::item:hover {{ background:{p['BG2']}; }}"
        f"{selector}::item:selected {{ background:{p['SELECTION']};"
        f"color:{p['SELECTION_TEXT']}; border:none; outline:none; }}"
        f"{selector}::item:selected:focus {{ background:{p['SELECTION']};"
        f"color:{p['SELECTION_TEXT']}; border:none; outline:none; }}"
        f"{selector}::item:focus {{ border:none; outline:none; }}"
        f"{header_selector} {{ background:{surface}; color:{p['TEXT_DIM']};"
        f"border:none; border-bottom:1px solid {line};"
        f"font-size:{meta_font_pt()}px; padding:6px 8px; font-weight:600; }}"
    )


def popover_frame_style(
    *,
    selector: str = "QFrame",
    border_radius: int = 8,
    theme: str | None = None,
) -> str:
    p = palette(theme)
    return (
        f"{selector} {{ background:{p['BG2']}; border:1px solid {p['BORDER']};"
        f"border-radius:{border_radius}px; }}"
    )


def popover_list_style(
    *,
    selector: str = "QListWidget",
    item_padding: str = "8px 10px",
    item_radius: int = 4,
    theme: str | None = None,
) -> str:
    """Transient picker — accent fill on selection (skills, @ mentions)."""
    p = palette(theme)
    return (
        f"{selector} {{ background:transparent; border:none; outline:none; }}"
        f"{selector}::item {{ padding:{item_padding}; border-radius:{item_radius}px; }}"
        f"{selector}::item:hover {{ background:{p['BG3']}; }}"
        f"{selector}::item:selected {{ background:{ACCENT}; color:white; }}"
        f"{selector}::item:selected:focus {{ background:{ACCENT}; color:white; }}"
        f"{selector}::item:focus {{ border:none; outline:none; }}"
    )


def data_table_style(
    *,
    selector: str = "QTableWidget",
    header_selector: str = "QHeaderView::section",
    border_radius: int = 6,
    theme: str | None = None,
) -> str:
    p = palette(theme)
    return (
        f"{selector} {{ background:{p['BG2']}; color:{p['TEXT']};"
        f"border:1px solid {p['BORDER_SUBTLE']}; border-radius:{border_radius}px;"
        "gridline-color:transparent; outline:none; }"
        f"{selector}::item {{ padding:6px 8px; border:none; }}"
        f"{selector}::item:selected {{ background:{p['SELECTION']};"
        f"color:{p['SELECTION_TEXT']}; }}"
        f"{header_selector} {{ background:{p['BG3']}; color:{p['TEXT_DIM']};"
        f"border:none; border-bottom:1px solid {p['BORDER_SUBTLE']};"
        f"font-size:{meta_font_pt()}px; padding:6px 8px; font-weight:600; }}"
    )


def splitter_style(
    *,
    selector: str = "QSplitter",
    handle_px: int = 1,
    theme: str | None = None,
) -> str:
    p = palette(theme)
    return (
        f"{selector} {{ background:{p['BG2']}; }}"
        f"{selector}::handle {{ background:{p['BORDER_SUBTLE']};"
        f"width:{handle_px}px; height:{handle_px}px; }}"
        f"{selector}::handle:hover {{ background:{p['BORDER']}; }}"
    )


def files_header_style() -> str:
    p = palette()
    fs = max(11, chat_font_pt() - 2)
    return (
        f"QWidget#filesHeader {{ background:{p['BG2']};"
        f"border-bottom:1px solid {p['BORDER_SUBTLE']}; }}"
        f"{hint_label_style(selector='QLabel#filesPath')}"
        f"QLineEdit#filesFilter {{ background:{p['BG3']}; color:{p['TEXT']};"
        f"border:1px solid {p['BORDER_SUBTLE']}; border-radius:{FIELD_BORDER_RADIUS}px;"
        f"padding:4px 8px; font-size:{fs}px; }}"
        f"QLineEdit#filesFilter:focus {{ border:1px solid {ACCENT}; }}"
    )


def search_field_style() -> str:
    p = palette()
    fs = max(12, chat_font_pt() - 1)
    bg = "#151922" if current_theme() != "light" else p["INPUT_BG"]
    border = "#202a34" if current_theme() != "light" else p["BORDER_SUBTLE"]
    return (
        f"QLineEdit {{ background:{bg}; color:{p['TEXT']};"
        f"border:1px solid {border}; border-radius:{SEARCH_FIELD_BORDER_RADIUS}px;"
        f"margin:2px 14px 8px 14px; padding:7px 11px; font-size:{fs}px; }}"
        f"QLineEdit:focus {{ border:1px solid {ACCENT}; }}"
    )


def overlay_dialog_style() -> str:
    p = palette()
    return (
        f"QDialog {{ background:{p['BG2']}; color:{p['TEXT']};"
        f"border:1px solid {p['BORDER']}; border-radius:{MODAL_BORDER_RADIUS}px; }}"
    )


def overlay_search_input_style() -> str:
    p = palette()
    return (
        f"QLineEdit {{ background:{p['BG3']}; color:{p['TEXT']};"
        f"border:1px solid {p['BORDER']}; border-radius:{OVERLAY_SEARCH_BORDER_RADIUS}px;"
        f"padding:10px 14px; font-size:{chat_font_pt()}px; }}"
        f"QLineEdit:focus {{ border:1px solid {ACCENT}; }}"
    )


def overlay_separator_style() -> str:
    return separator_frame_style()


def overlay_results_list_style() -> str:
    """Modal results — left accent bar on selection (search, command palette)."""
    p = palette()
    return _flat_list_style(
        selected_bg=p["BG3"],
        selected_border_left=f"3px solid {ACCENT}",
        include_focus_reset=False,
    )


def conversation_list_style() -> str:
    """Conversation sidebar — rounded rows, softer selection fill."""
    p = palette()
    sel = "#1d2d4d" if current_theme() != "light" else list_selection_bg()
    hover = "#171b24" if current_theme() != "light" else p["BG3"]
    return _flat_list_style(
        item_radius=7,
        item_margin="1px 7px",
        hover_bg=hover,
        selected_bg=sel,
        include_focus_reset=False,
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


def file_tab_style(object_name: str = "fileViewerTabs") -> str:
    p = palette()
    return (
        flat_tab_style(object_name)
        + f"QTabWidget#{object_name} QTabBar::tab {{ padding:6px 10px;"
        f"min-width:88px; max-width:220px; background:{p['BG2']}; }}"
        f"QTabWidget#{object_name} QTabBar::close-button {{ margin-left:6px; }}"
    )


def apply_flat_tab_style(tabs, object_name: str) -> None:
    tabs.setObjectName(object_name)
    tabs.setDocumentMode(True)
    tabs.tabBar().setDrawBase(False)
    tabs.setStyleSheet(flat_tab_style(object_name))


def sidebar_tab_style() -> str:
    return flat_tab_style("sidebarTabs")


def navigation_list_style(
    *,
    selector: str = "QListWidget",
    bg: str | None = None,
    border: str | None = None,
    item_padding: str = "10px 12px 10px 15px",
    item_radius: int = 6,
    selected_bg: str | None = None,
    hover_bg: str | None = None,
    theme: str | None = None,
) -> str:
    """Vertical section nav — dim items, accent left bar (settings sidebar)."""
    p = palette(theme)
    surface = bg or p["BG"]
    line = border if border is not None else f"0px solid {p['BORDER_SUBTLE']}"
    border_rule = line if "border:" in line else f"border:{line};"
    selected = selected_bg or p["BG3"]
    hover = hover_bg or p["BG2"]
    return (
        f"{selector} {{ background:{surface}; {border_rule}"
        "padding:8px 6px; outline:none; }"
        f"{selector}::item {{ color:{p['TEXT_DIM']}; padding:{item_padding};"
        f"border-radius:{item_radius}px; border-left:3px solid transparent; }}"
        f"{selector}::item:hover {{ background:{hover}; }}"
        f"{selector}::item:selected {{ background:{selected}; color:{p['TEXT']};"
        f"border-left:3px solid {ACCENT}; }}"
        f"{selector}::item:selected:focus {{ background:{selected}; color:{p['TEXT']};"
        f"border-left:3px solid {ACCENT}; outline:none; }}"
        f"{selector}::item:focus {{ outline:none; }}"
    )


def sidebar_footer_button_style(
    *,
    selector: str = "QPushButton",
    theme: str | None = None,
) -> str:
    """Bottom-of-rail icon buttons (extensions, search, docs, settings)."""
    p = palette(theme)
    fs = meta_font_pt()
    return (
        f"{selector} {{ background:transparent; color:{p['TEXT_DIM']}; border:none;"
        f"border-radius:7px; padding:6px 2px; font-size:{fs}px; }}"
        f"{selector}:hover {{ background:{p['BG3']}; color:{p['TEXT']}; }}"
    )


def tone_badge_button_style(
    tone: str = "",
    *,
    theme: str | None = None,
) -> str:
    p = palette(theme)
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


def extension_list_row_style(
    *,
    selected: bool,
    tone: str = "",
    object_name: str = "extensionListRow",
) -> str:
    p = palette()
    bg = p["SELECTION"] if selected else p["BG2"]
    hover = p["SELECTION"] if selected else p["BG3"]
    border = {
        "danger": "#5f252d",
        "disabled": p["BORDER_SUBTLE"],
    }.get(tone, p["BORDER_SUBTLE"])
    return (
        f"QFrame#{object_name} {{ background-color:{bg};"
        f"border-bottom:1px solid {border}; border-radius:0; }}"
        f"QFrame#{object_name} QLabel {{ background-color:transparent; border:none; }}"
        f"QFrame#{object_name}:hover {{ background-color:{hover}; }}"
    )


def extension_header_frame_style(*, object_name: str = "extensionHeader") -> str:
    p = palette()
    return (
        f"QFrame#{object_name} {{ background:transparent;"
        f"border-bottom:1px solid {p['BORDER_SUBTLE']}; border-radius:0; }}"
    )


def extension_detail_table_frame_style(*, tone: str = "", object_name: str = "extensionDetailTable") -> str:
    p = palette()
    border = "#5f252d" if tone == "danger" else p["BORDER_SUBTLE"]
    return (
        f"QFrame#{object_name} {{ background:transparent;"
        f"border-top:1px solid {border}; border-radius:0; }}"
    )


def extension_list_name_style() -> str:
    return title_label_style(font_weight="600", font_pt=chat_font_pt())


def extension_list_meta_style(tone: str = "") -> str:
    p = palette()
    color = {
        "danger": "#f87171",
        "disabled": p["TEXT_DIM"],
        "success": p["SUCCESS"],
    }.get(tone, p["TEXT_DIM"])
    return section_label_style(text_color=color)


def extension_detail_name_style(*, tone: str = "") -> str:
    p = palette()
    color = "#f87171" if tone == "danger" else p["TEXT"]
    return title_label_style(text_color=color, font_weight="600", font_pt=chat_font_pt())


def extension_detail_value_style(*, tone: str = "") -> str:
    p = palette()
    color = "#fca5a5" if tone == "danger" else p["TEXT_DIM"]
    return hint_label_style(text_color=color)


def extension_panel_heading_style(*, tone: str = "") -> str:
    color = "#f87171" if tone == "danger" else None
    return section_label_style(text_color=color)


def combo_box_popup_style(
    theme: str | None = None,
    *,
    bg: str | None = None,
    border_radius: int = 8,
    font_pt: int | None = None,
    view_padding: int = 4,
    item_padding: str = "6px 10px",
) -> str:
    p = palette(theme)
    surface = bg or p["BG3"]
    fs = font_pt or chat_font_pt()
    min_height = max(24, fs + 8)
    return (
        f"QComboBoxPrivateContainer {{ background:{surface}; border:none;"
        f"margin:0; padding:0; outline:none; }}"
        f"QComboBoxPrivateContainer QWidget {{ background:{surface}; border:none; }}"
        f"QComboBox QAbstractItemView, QComboBox QListView {{ "
        f"background:{surface}; alternate-background-color:{surface};"
        f"color:{p['TEXT']}; border:1px solid {p['BORDER']};"
        f"border-radius:{border_radius}px; outline:none; margin:0;"
        f"padding:{view_padding}px;"
        f"selection-background-color:{p['SELECTION']};"
        f"selection-color:{p['SELECTION_TEXT']}; font-size:{fs}px; }}"
        f"QComboBox QAbstractItemView::item, QComboBox QListView::item {{ "
        f"background:{surface}; color:{p['TEXT']}; padding:{item_padding};"
        f"min-height:{min_height}px; border:none; border-radius:4px; }}"
        f"QComboBox QAbstractItemView::item:hover, QComboBox QListView::item:hover {{ "
        f"background:{p['BG2']}; color:{p['TEXT']}; }}"
        f"QComboBox QAbstractItemView::item:selected, QComboBox QListView::item:selected {{ "
        f"background:{p['SELECTION']}; color:{p['SELECTION_TEXT']}; }}"
        f"QComboBoxPrivateContainer QListView::indicator, "
        f"QComboBox QAbstractItemView::indicator {{ width:0; height:0; border:none;"
        f"background:transparent; margin:0; padding:0; }}"
    )


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
    border-radius:{FIELD_BORDER_RADIUS}px; padding:6px 10px; font-size:{fs}px;
}}
QLineEdit:focus {{ border:1px solid {ACCENT}; }}
{combo_box_field_style(theme=theme, font_pt=fs, padding_v="4px", padding_h="10px", border_radius=FIELD_BORDER_RADIUS, drop_down_width=20)}
{combo_box_popup_style(theme, font_pt=fs, border_radius=FIELD_BORDER_RADIUS)}
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
{menu_style(theme=theme)}
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
        f"QFrame#composerShell[composerFocused=\"true\"] {{ border:1px solid {shell_focus}; }}"
    )


def edit_bubble_style(font_pt: int | None = None) -> str:
    p = palette()
    fs = font_pt or chat_font_pt()
    return (
        f"background:{p['BG3']}; color:{p['TEXT']}; border:1px solid {ACCENT};"
        f"border-radius:10px; padding:8px 12px; font-size:{fs}px;"
    )


def _install_combo_popup_filter(app) -> None:
    from PyQt6.QtCore import QEvent, QObject

    if not hasattr(app, "installEventFilter"):
        return
    if app.property("aichsComboPopupFilterInstalled"):
        return

    class _ComboPopupFilter(QObject):
        def eventFilter(self, obj, event):
            if event.type() != QEvent.Type.Show:
                return False
            if obj.metaObject().className() != "QComboBoxPrivateContainer":
                return False
            colors = palette()
            surface = colors["BG3"]
            obj.setStyleSheet(
                f"background:{surface}; border:none; margin:0; padding:0;"
                f"QWidget {{ background:{surface}; border:none; }}"
            )
            return False

    app.installEventFilter(_ComboPopupFilter(app))
    app.setProperty("aichsComboPopupFilterInstalled", True)


def apply_app_theme(app, theme: str | None = None) -> None:
    from ui.win_caption import install_caption_sync, sync_all_windows_captions

    _install_combo_popup_filter(app)

    theme_name = theme or current_theme()
    font = app_font()
    theme_key = f"{theme_name}:{font.family()}:{font.pointSize()}"
    cached_sheet = app.property("aichsThemeStyleSheet")
    current_font = app.font()
    font_matches = (
        current_font.family() == font.family()
        and current_font.pointSize() == font.pointSize()
    )
    if (
        app.property("aichsThemeKey") != theme_key
        or not font_matches
        or not cached_sheet
        or app.styleSheet() != cached_sheet
    ):
        sheet = build_stylesheet(theme_name)
        app.setFont(font)
        app.setStyleSheet(sheet)
        app.setProperty("aichsThemeKey", theme_key)
        app.setProperty("aichsThemeStyleSheet", sheet)
    install_caption_sync(app)
    sync_all_windows_captions(app, theme_name)


DARK_STYLE = build_stylesheet("dark")
