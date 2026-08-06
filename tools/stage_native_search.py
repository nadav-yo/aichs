"""Stage the native search & terminal helpers for a platform-specific Python wheel."""

from __future__ import annotations

try:
    from tools.native_search_tools import ROOT, stage_native_search_tools, stage_native_terminal
except ModuleNotFoundError:  # Direct ``python tools/stage_native_search.py`` execution.
    from native_search_tools import ROOT, stage_native_search_tools, stage_native_terminal


if __name__ == "__main__":
    dest = ROOT / "aichs_native" / "bin"
    stage_native_search_tools(dest)
    stage_native_terminal(dest)

