#!/usr/bin/env python3
"""Promote CHANGELOG.md ``## Next`` to ``## <version>`` for releases."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


_NEXT_SECTION = re.compile(r"(?ms)^## Next\n(.*?)(?=^## |\Z)")


def promote_changelog(text: str, version: str) -> str:
    """Rename the Next section to ``version`` and open a fresh empty Next.

    Raises:
        ValueError: if Next is missing/empty or ``version`` already exists.
    """
    version = version.strip()
    if not version:
        raise ValueError("Version is empty.")
    if re.search(rf"(?m)^## {re.escape(version)}\s*$", text):
        raise ValueError(f"CHANGELOG.md already has ## {version}.")
    match = _NEXT_SECTION.search(text)
    if not match:
        raise ValueError("CHANGELOG.md is missing a ## Next section.")
    body = match.group(1).strip()
    if not body:
        raise ValueError(
            "CHANGELOG.md ## Next is empty; add release notes before releasing."
        )
    promoted = f"## Next\n\n## {version}\n\n{body}\n\n"
    updated = text[: match.start()] + promoted + text[match.end() :]
    return re.sub(r"\n{3,}", "\n\n", updated).rstrip() + "\n"


def section_body(text: str, heading: str) -> str:
    """Return the body under ``## <heading>``, or ``\"\"`` if missing."""
    match = re.search(
        rf"(?ms)^## {re.escape(heading)}\n(.*?)(?=^## |\Z)",
        text,
    )
    return match.group(1).strip() if match else ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="PEP 440 version without a leading v")
    parser.add_argument(
        "--path",
        type=Path,
        default=Path("CHANGELOG.md"),
        help="Path to CHANGELOG.md (default: ./CHANGELOG.md)",
    )
    parser.add_argument(
        "--print-notes",
        action="store_true",
        help="Print the promoted version section body to stdout (after writing)",
    )
    args = parser.parse_args(argv)

    path: Path = args.path
    if not path.is_file():
        print(f"{path} is missing.", file=sys.stderr)
        return 1
    try:
        updated = promote_changelog(path.read_text(encoding="utf-8"), args.version)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    path.write_text(updated, encoding="utf-8")
    if args.print_notes:
        print(section_body(updated, args.version.strip()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
