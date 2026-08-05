"""Check PyPI for newer aichs releases."""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

PACKAGE_NAME = "aichs"
PYPI_JSON_URL = f"https://pypi.org/pypi/{PACKAGE_NAME}/json"
UPGRADE_COMMAND = "pipx upgrade aichs"
DEFAULT_CHECK_INTERVAL_SEC = 24 * 60 * 60
_REQUEST_TIMEOUT_SEC = 8.0
_VERSION_RE = re.compile(
    r"""
    ^\s*
    (?:(?P<epoch>\d+)!)?
    (?P<release>\d+(?:\.\d+)*)
    (?P<pre>(?:a|b|rc)\d+)?
    (?:\.?(?:post|dev)\d+)*
    (?:\+[^\s]+)?
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)


@dataclass(frozen=True)
class UpdateAvailability:
    installed: str
    latest: str

    @property
    def upgrade_command(self) -> str:
        return UPGRADE_COMMAND


def is_frozen_app() -> bool:
    return bool(getattr(sys, "frozen", False))


def installed_version(*, package: str = PACKAGE_NAME) -> str:
    try:
        from importlib.metadata import PackageNotFoundError, version
    except ImportError:  # pragma: no cover
        PackageNotFoundError = Exception  # type: ignore[misc, assignment]
        version = None  # type: ignore[assignment]

    if version is not None:
        try:
            return str(version(package)).strip()
        except PackageNotFoundError:
            pass
        except Exception:
            pass
    return _version_from_pyproject() or "0"


def _version_from_pyproject() -> str:
    root = Path(__file__).resolve().parents[1]
    path = root / "pyproject.toml"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    return str(match.group(1)).strip() if match else ""


def parse_version_key(value: str) -> tuple:
    """Return a comparable key for common PEP 440 release versions."""
    text = str(value or "").strip()
    if text.lower().startswith("v") and text[1:2].isdigit():
        text = text[1:]
    match = _VERSION_RE.match(text)
    if not match:
        raise ValueError(f"Unsupported version: {value!r}")
    epoch = int(match.group("epoch") or 0)
    release = tuple(int(part) for part in match.group("release").split("."))
    pre = match.group("pre")
    if pre:
        kind = pre[:1].lower()
        # a < b < rc < final
        rank = {"a": 0, "b": 1, "r": 2}.get(kind, 0)
        num = int(re.sub(r"\D", "", pre) or 0)
        pre_key = (0, rank, num)
    else:
        pre_key = (1, 0, 0)
    return (epoch, release, pre_key)


def is_newer(latest: str, installed: str) -> bool:
    try:
        return parse_version_key(latest) > parse_version_key(installed)
    except ValueError:
        return False


def fetch_latest_version(
    *,
    url: str = PYPI_JSON_URL,
    timeout: float = _REQUEST_TIMEOUT_SEC,
    opener=None,
) -> str:
    open_url = urllib.request.urlopen if opener is None else opener
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": f"aichs-update-check/{installed_version()}",
        },
    )
    with open_url(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    info = payload.get("info") if isinstance(payload, dict) else None
    latest = ""
    if isinstance(info, dict):
        latest = str(info.get("version") or "").strip()
    if not latest:
        raise ValueError("PyPI response missing info.version")
    return latest


def check_for_update(
    *,
    installed: str | None = None,
    fetch=fetch_latest_version,
) -> UpdateAvailability | None:
    """Return availability when latest is newer than installed; else None.

    Network/parse failures return None (quiet).
    """
    current = (installed if installed is not None else installed_version()).strip() or "0"
    try:
        latest = str(fetch() or "").strip()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError, TypeError):
        return None
    except Exception:
        return None
    if not latest or not is_newer(latest, current):
        return None
    return UpdateAvailability(installed=current, latest=latest)


def should_prompt(availability: UpdateAvailability | None, dismissed_version: str) -> bool:
    if availability is None:
        return False
    dismissed = str(dismissed_version or "").strip()
    if dismissed and dismissed == availability.latest:
        return False
    return True


def should_run_network_check(
    *,
    enabled: bool,
    last_checked: float,
    now: float | None = None,
    interval_sec: int = DEFAULT_CHECK_INTERVAL_SEC,
    frozen: bool | None = None,
) -> bool:
    if not enabled:
        return False
    if frozen if frozen is not None else is_frozen_app():
        return False
    stamp = float(last_checked or 0)
    if stamp <= 0:
        return True
    current = time.time() if now is None else float(now)
    return (current - stamp) >= float(interval_sec)


def mark_checked(data: dict, *, now: float | None = None) -> dict:
    updated = dict(data)
    updated["update_last_checked"] = float(time.time() if now is None else now)
    return updated


def dismiss_version(data: dict, version: str) -> dict:
    updated = dict(data)
    updated["update_dismissed_version"] = str(version or "").strip()
    return updated
