import base64
import html
import re

import markdown as _md

from ui.theme import markdown_code_block_styles


COPY_CODE_SCHEME = "aichs-copy-code"

_CODE_BLOCK_RE = re.compile(
    r"<pre><code(?P<attrs>[^>]*)>(?P<code>.*?)</code></pre>",
    re.DOTALL,
)


def markdown_body(
    markdown_text: str,
    extensions: list[str],
    *,
    theme: str | None = None,
    font_pt: int | None = None,
) -> str:
    body = _md.markdown(markdown_text, extensions=extensions)
    return _render_code_blocks_as_tables(body, theme=theme, font_pt=font_pt)


def _render_code_blocks_as_tables(
    html_text: str,
    *,
    theme: str | None = None,
    font_pt: int | None = None,
) -> str:
    styles = markdown_code_block_styles(theme=theme, font_pt=font_pt)

    def repl(match: re.Match) -> str:
        code = match.group("code")
        copy_href = html.escape(copy_code_url(html.unescape(code)), quote=True)
        return (
            '<table class="aichs-code-block" width="100%" cellspacing="0" cellpadding="0" '
            f'style="{styles["table"]}">'
            f'<tr><td style="{styles["header"]}">'
            f'<a href="{copy_href}" title="Copy code" style="{styles["copy"]}">&#x29c9;</a>'
            "</td></tr>"
            f'<tr><td style="{styles["cell"]}">'
            f'<pre style="{styles["pre"]}"><span style="{styles["text"]}">{code}</span></pre>'
            "</td></tr></table>"
        )

    return _CODE_BLOCK_RE.sub(repl, html_text)


def copy_code_url(code: str) -> str:
    payload = base64.urlsafe_b64encode(code.encode("utf-8")).decode("ascii").rstrip("=")
    return f"{COPY_CODE_SCHEME}:{payload}"


def code_from_copy_url(url: str) -> str | None:
    prefix = f"{COPY_CODE_SCHEME}:"
    if not str(url).startswith(prefix):
        return None
    payload = str(url)[len(prefix):]
    if not payload:
        return ""
    padded = payload + "=" * (-len(payload) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except Exception:
        return None


def is_copy_code_url(url: str) -> bool:
    return code_from_copy_url(url) is not None
