from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import (
    get_lexer_for_filename, get_lexer_by_name, guess_lexer, TextLexer,
)

from ui.theme import current_theme, palette, mono_font_pt, MONO_FONT_CSS


def _formatter() -> HtmlFormatter:
    theme = current_theme()
    style = "default" if theme == "light" else "monokai"
    bg = palette(theme)["BG3"]
    fs = mono_font_pt()
    return HtmlFormatter(
        style=style,
        noclasses=True,
        prestyles=(
            f"font-family: {MONO_FONT_CSS}; "
            f"font-size: {fs}px; line-height: 1.5; margin: 0; padding: 12px;"
            f"background: {bg};"
        ),
    )


def for_path(content: str, path: str) -> str:
    try:
        lexer = get_lexer_for_filename(path, stripall=True)
    except Exception:
        try:
            lexer = guess_lexer(content)
        except Exception:
            lexer = TextLexer()
    return highlight(content, lexer, _formatter())


def for_language(content: str, language: str) -> str:
    try:
        lexer = get_lexer_by_name(language, stripall=True) if language else TextLexer()
    except Exception:
        lexer = TextLexer()
    return highlight(content, lexer, _formatter())
