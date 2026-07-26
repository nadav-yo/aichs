"""Locate the Ripgrep binary shipped with aichs or supplied by a developer."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from aichs_native import binary_path


_ROOT = Path(__file__).resolve().parents[1]


def ripgrep_path() -> str | None:
    """Return a supported Ripgrep executable without requiring it on ``PATH``.

    Packaged builds take precedence over a developer installation so users get the
    version we test and ship.  A PATH lookup remains useful for source checkouts.
    """

    for path in _bundled_candidates():
        if path.is_file():
            return str(path)
    return shutil.which("rg")


def _bundled_candidates() -> tuple[Path, ...]:
    executable = "rg.exe" if sys.platform == "win32" else "rg"
    platform = _platform_name()
    roots = [
        Path(getattr(sys, "_MEIPASS", "")),
        Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(),
        _ROOT,
    ]
    candidates = [
        root / relative
        for root in roots
        if str(root)
        for relative in (
            Path("bin") / executable,
            Path("tools") / "vendor" / "ripgrep" / platform / executable,
        )
    ]
    wheel_binary = binary_path(executable)
    if wheel_binary is not None:
        candidates.append(wheel_binary)
    return tuple(candidates)


def _platform_name() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"
