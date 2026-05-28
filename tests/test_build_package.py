import struct

from tools import build_package


def test_write_windows_ico_from_png_assets(tmp_path, monkeypatch):
    png_dir = tmp_path / "png"
    png_dir.mkdir()
    png_data = b"\x89PNG\r\n\x1a\nfake-png"
    for size in (16, 32, 256):
        (png_dir / f"icon-{size}.png").write_bytes(png_data + bytes([size % 255]))
    monkeypatch.setattr(build_package, "PNG_DIR", png_dir)
    monkeypatch.setattr(build_package, "ICON_SIZES", (16, 32, 256))

    out = tmp_path / "app.ico"
    build_package._write_windows_ico(out)

    data = out.read_bytes()
    reserved, icon_type, count = struct.unpack("<HHH", data[:6])
    assert (reserved, icon_type, count) == (0, 1, 3)
    first_width, first_height = data[6], data[7]
    third_width, third_height = data[6 + 16 * 2], data[7 + 16 * 2]
    assert (first_width, first_height) == (16, 16)
    assert (third_width, third_height) == (0, 0)
    assert png_data in data
