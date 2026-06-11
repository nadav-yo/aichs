"""Render file-change views as themed HTML for QTextEdit."""

from __future__ import annotations

import html

from ui.theme import ACCENT, MONO_FONT_CSS, current_theme, mono_font_pt, palette


def diff_to_html(unified_diff: str, theme: str | None = None) -> str:
    p = palette(theme)
    fs = mono_font_pt()
    bg = p["BG3"]
    add_bg = p["SUCCESS_BG"]
    add_fg = p["SUCCESS"]
    del_bg = "#fef2f2" if _theme_name(theme) == "light" else "#2a1518"
    del_fg = "#f87171"
    meta = p["TEXT_DIM"]

    rows: list[str] = []
    for line in unified_diff.splitlines():
        esc = html.escape(line)
        if line.startswith("+++") or line.startswith("---"):
            style = f"color:{meta};"
        elif line.startswith("@@"):
            style = f"color:{ACCENT};"
        elif line.startswith("+"):
            style = f"background:{add_bg}; color:{add_fg};"
        elif line.startswith("-"):
            style = f"background:{del_bg}; color:{del_fg};"
        else:
            style = f"color:{p['TEXT']};"
        rows.append(f'<div style="{style} white-space:pre;">{esc}</div>')

    body = "\n".join(rows) if rows else f'<div style="color:{meta};">(no differences)</div>'
    return (
        f'<div style="background:{p["BG3"]}; color:{p["TEXT"]}; margin:0; padding:0;">'
        f'<pre style="font-family:{MONO_FONT_CSS}; font-size:{fs}px; line-height:1.5;'
        f'margin:0; padding:12px; background:{bg};">{body}</pre>'
        "</div>"
    )


def inline_new_file_diff_to_html(
    unified_diff: str,
    content: str = "",
    theme: str | None = None,
) -> str:
    """Render the full current file with changed new-file lines highlighted."""
    p = palette(theme)
    fs = mono_font_pt()
    bg = p["BG3"]
    add_bg = p["SUCCESS_BG"]
    add_fg = p["SUCCESS"]
    meta = p["TEXT_DIM"]

    changed_lines = _changed_new_line_numbers(unified_diff)

    rows: list[str] = []
    for line_no, text in enumerate(content.splitlines(), start=1):
        esc = html.escape(text)
        if line_no in changed_lines:
            style = f"background:{add_bg}; color:{add_fg};"
        else:
            style = f"color:{p['TEXT']};"
        rows.append(f'<div style="{style} white-space:pre;">{esc}</div>')

    if content.endswith("\n"):
        rows.append(f'<div style="color:{p["TEXT"]}; white-space:pre;"></div>')

    body = "\n".join(rows) if rows else f'<div style="color:{meta};">(empty file)</div>'
    return (
        f'<div style="background:{p["BG3"]}; color:{p["TEXT"]}; margin:0; padding:0;">'
        f'<pre style="font-family:{MONO_FONT_CSS}; font-size:{fs}px; line-height:1.5;'
        f'margin:0; padding:12px; background:{bg};">{body}</pre>'
        "</div>"
    )


def changed_new_line_numbers(unified_diff: str) -> set[int]:
    return _changed_new_line_numbers(unified_diff)


def _theme_name(theme: str | None = None) -> str:
    return theme or current_theme()


def _changed_new_line_numbers(unified_diff: str) -> set[int]:
    changed: set[int] = set()
    new_line = 0
    for line in unified_diff.splitlines():
        if line.startswith("@@"):
            marker = line.split("@@", 2)[1].strip()
            new_part = next((p for p in marker.split() if p.startswith("+")), "")
            start = new_part[1:].split(",", 1)[0]
            try:
                new_line = int(start)
            except ValueError:
                new_line = 0
            continue
        if not new_line or line.startswith(("diff --git", "index ", "--- ", "+++ ")):
            continue
        if line.startswith("+"):
            changed.add(new_line)
            new_line += 1
        elif line.startswith(" "):
            new_line += 1
        elif line.startswith("-"):
            continue
        elif line.startswith("\\"):
            continue
    return changed
