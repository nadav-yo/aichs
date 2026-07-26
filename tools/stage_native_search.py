"""Stage the native search helpers for a platform-specific Python wheel."""

from __future__ import annotations

try:
    from tools.native_search_tools import ROOT, stage_native_search_tools
except ModuleNotFoundError:  # Direct ``python tools/stage_native_search.py`` execution.
    from native_search_tools import ROOT, stage_native_search_tools


if __name__ == "__main__":
    stage_native_search_tools(ROOT / "aichs_native" / "bin")
