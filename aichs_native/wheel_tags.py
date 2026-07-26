"""Wheel-tag helpers for artifacts that bundle native search binaries."""

from __future__ import annotations

import os


def platform_tag(default: str) -> str:
    """Return the build platform tag, honoring the release-build override."""

    return os.environ.get("AICHS_WHEEL_PLATFORM_TAG", "").strip() or default
