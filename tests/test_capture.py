from PIL import Image
from capture import (
    capture_fullscreen, capture_region, capture_virtual_screen_size,
    _build_dib,
)


def test_capture_fullscreen_returns_image():
    img = capture_fullscreen()
    assert isinstance(img, Image.Image)
    assert img.width > 0 and img.height > 0


def test_capture_fullscreen_multiple_monitors():
    img = capture_fullscreen()
    assert img.size == capture_virtual_screen_size()


def test_capture_region():
    full = capture_fullscreen()
    w, h = full.size
    region = capture_region(0, 0, w // 2, h // 2)
    assert isinstance(region, Image.Image)
    assert region.size == (w // 2, h // 2)


def _reference_dib(img):
    """旧逐行 Python 翻转的参考实现，用于字节对拍。"""
    import struct
    im = img.convert("RGB") if img.mode != "RGB" else img
    w, h = im.size
    pixels = im.tobytes("raw", "BGRX")
    row = w * 4
    pixels_bu = b"".join(pixels[i * row:(i + 1) * row] for i in range(h - 1, -1, -1))
    header = struct.pack("<IiiHHIIiiII", 40, w, h, 1, 32, 0,
                         len(pixels_bu), 0, 0, 0, 0)
    return header + pixels_bu


def test_build_dib_matches_row_flip_reference():
    img = Image.new("RGB", (3, 2), (0, 0, 0))
    for x in range(3):
        for y in range(2):
            img.putpixel((x, y), (x * 80, y * 120, (x + y) * 40))
    assert _build_dib(img) == _reference_dib(img)


def test_build_dib_non_rgb_input():
    import struct
    img = Image.new("RGBA", (2, 2), (10, 20, 30, 255))
    dib = _build_dib(img)
    assert len(dib) == 40 + 2 * 2 * 4
    w, h = struct.unpack("<ii", dib[4:12])
    assert (w, h) == (2, 2)
