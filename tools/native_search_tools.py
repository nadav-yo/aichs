"""Build and stage the native helpers included in distributable artifacts."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def stage_native_search_tools(destination: Path) -> tuple[Path, Path]:
    """Build the filename indexer and copy it plus Ripgrep into ``destination``."""

    destination.mkdir(parents=True, exist_ok=True)
    rg = shutil.which("rg")
    if not rg:
        raise RuntimeError(
            "Ripgrep is required to create a distributable package. Install rg on the build machine; "
            "end users receive the bundled executable."
        )
    cargo = shutil.which("cargo")
    if not cargo:
        raise RuntimeError("Cargo is required to build the bundled aichs filename indexer.")

    subprocess.check_call(
        [cargo, "build", "--release", "--manifest-path", "rust/aichs-indexer/Cargo.toml"],
        cwd=ROOT,
    )
    suffix = ".exe" if sys.platform == "win32" else ""
    indexer = ROOT / "rust" / "aichs-indexer" / "target" / "release" / f"aichs-indexer{suffix}"
    rg_destination = destination / f"rg{suffix}"
    indexer_destination = destination / f"aichs-indexer{suffix}"
    shutil.copy2(rg, rg_destination)
    shutil.copy2(indexer, indexer_destination)
    return rg_destination, indexer_destination
