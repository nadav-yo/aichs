import argparse
import shutil
import struct
import subprocess
import sys
from pathlib import Path

try:
    from tools.native_search_tools import stage_native_search_tools
except ModuleNotFoundError:  # Direct ``python tools/build_package.py`` execution.
    from native_search_tools import stage_native_search_tools

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
VENDOR_DIR = ROOT / "tools" / "vendor"
PNG_DIR = ASSETS / "png"
ICON_BUILD_DIR = ROOT / "build" / "icons"
WINDOWS_ICO = ICON_BUILD_DIR / "app.ico"
MACOS_ICNS = ICON_BUILD_DIR / "app.icns"
ICON_SIZES = (16, 32, 64, 128, 256, 512)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a packaged aichs desktop app.")
    parser.add_argument(
        "--prepare-icons-only",
        action="store_true",
        help="Generate platform icon assets and exit without running PyInstaller.",
    )
    parser.add_argument(
        "--no-clean",
        action="store_true",
        help="Do not ask PyInstaller to clean its temporary build cache.",
    )
    args = parser.parse_args()

    _prepare_icons()
    if args.prepare_icons_only:
        return 0
    _prepare_native_search_tools()

    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm"]
    if not args.no_clean:
        cmd.append("--clean")
    cmd.append("aichs.spec")
    return subprocess.call(cmd, cwd=ROOT)


def _prepare_icons() -> None:
    _write_windows_ico(WINDOWS_ICO)
    if sys.platform == "darwin":
        _write_macos_icns(MACOS_ICNS)


def _prepare_native_search_tools() -> None:
    """Stage the native search executables that the packaged desktop app owns."""

    platform = _platform_name()
    rg_src, indexer_src = stage_native_search_tools(ROOT / "aichs_native" / "bin")
    rg_dest = VENDOR_DIR / "ripgrep" / platform / rg_src.name
    indexer_dest = VENDOR_DIR / "aichs-indexer" / platform / indexer_src.name
    rg_dest.parent.mkdir(parents=True, exist_ok=True)
    indexer_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(rg_src, rg_dest)
    shutil.copy2(indexer_src, indexer_dest)


def _platform_name() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _write_windows_ico(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    images = []
    for size in ICON_SIZES:
        png = PNG_DIR / f"icon-{size}.png"
        if not png.exists():
            continue
        data = png.read_bytes()
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError(f"{png} is not a PNG file")
        images.append((size, data))
    if not images:
        raise FileNotFoundError(f"no icon PNGs found in {PNG_DIR}")

    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries = []
    payloads = []
    for size, data in images:
        width = 0 if size >= 256 else size
        height = 0 if size >= 256 else size
        entries.append(
            struct.pack(
                "<BBBBHHII",
                width,
                height,
                0,
                0,
                1,
                32,
                len(data),
                offset,
            )
        )
        payloads.append(data)
        offset += len(data)

    path.write_bytes(header + b"".join(entries) + b"".join(payloads))


def _write_macos_icns(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    iconutil = shutil.which("iconutil")
    if not iconutil:
        print("warning: iconutil not found; macOS .icns icon was not generated", file=sys.stderr)
        return

    iconset = ROOT / "build" / "aichs.iconset"
    iconset.mkdir(parents=True, exist_ok=True)
    pairs = [
        (16, "icon_16x16.png"),
        (32, "icon_16x16@2x.png"),
        (32, "icon_32x32.png"),
        (64, "icon_32x32@2x.png"),
        (128, "icon_128x128.png"),
        (256, "icon_128x128@2x.png"),
        (256, "icon_256x256.png"),
        (512, "icon_256x256@2x.png"),
        (512, "icon_512x512.png"),
    ]
    for size, name in pairs:
        src = PNG_DIR / f"icon-{size}.png"
        if src.exists():
            shutil.copyfile(src, iconset / name)

    subprocess.check_call([iconutil, "-c", "icns", "-o", str(path), str(iconset)], cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
