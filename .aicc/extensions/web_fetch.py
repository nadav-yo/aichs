from html.parser import HTMLParser
from urllib.request import Request, urlopen


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.skip = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"} and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            text = " ".join(data.split())
            if text:
                self.parts.append(text)


def register(registry):
    registry.tool(
        name="web_fetch",
        description="Fetch a web page and return readable text.",
        input_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "max_chars": {
                    "type": "integer",
                    "description": "Maximum characters to return.",
                    "default": 12000,
                },
            },
            "required": ["url"],
        },
        execute=web_fetch,
        approval="once",
        parallel_safe=True,
    )


def web_fetch(ctx, inputs):
    url = str(inputs["url"])
    max_chars = int(inputs.get("max_chars") or 12000)
    if not url.startswith(("http://", "https://")):
        return "[tool error] url must start with http:// or https://"

    req = Request(url, headers={"User-Agent": "aicc/extension"})
    with urlopen(req, timeout=20) as response:
        raw = response.read(max_chars * 4)
        charset = response.headers.get_content_charset() or "utf-8"

    html = raw.decode(charset, errors="replace")
    parser = TextExtractor()
    parser.feed(html)
    text = "\n".join(parser.parts)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n[truncated]"
    return text or "(no readable text)"