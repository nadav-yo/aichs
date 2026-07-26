"""Locations for optional native helpers bundled in platform wheels."""

from pathlib import Path


def binary_path(name: str) -> Path | None:
    """Return an installed native helper when this wheel contains it."""

    candidate = Path(__file__).with_name("bin") / name
    return candidate if candidate.is_file() else None
