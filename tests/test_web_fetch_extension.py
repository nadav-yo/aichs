import importlib.util
from pathlib import Path


def _load_web_fetch_module():
    path = Path(__file__).parents[1] / ".aichs" / "extensions" / "web_fetch.py"
    spec = importlib.util.spec_from_file_location("aichs_test_web_fetch", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Headers:
    def get_content_charset(self):
        return "utf-8"


class _Response:
    headers = _Headers()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self, _limit):
        return b"<html><body><script>hide()</script><h1>Hello</h1><p>World</p></body></html>"

    def geturl(self):
        return "https://example.com/final"


def test_web_fetch_returns_source_and_readable_text(monkeypatch):
    module = _load_web_fetch_module()
    monkeypatch.setattr(module, "urlopen", lambda _req, timeout: _Response())

    out = module.web_fetch(None, {"url": " https://example.com/start "})

    assert out.startswith("Source: https://example.com/final\n\n")
    assert "Hello" in out
    assert "World" in out
    assert "hide()" not in out


def test_web_fetch_requires_http_url():
    module = _load_web_fetch_module()

    assert "http:// or https://" in module.web_fetch(None, {"url": "file:///tmp/x"})
    assert "include a host" in module.web_fetch(None, {"url": "https://"})
