import os

import pytest
from PIL import Image
from capture import (
    capture_fullscreen, capture_region, capture_virtual_screen_size,
    _build_dib, copy_text_to_clipboard, get_desktop_path, default_save_path,
    save_image,
)


@pytest.fixture(scope="module")
def real_screen():
    """真实屏幕可用性探测。

    无显示会话（RDP 断开 / 无头环境）下 BitBlt 必然拿不到屏幕内容，
    整组真实截图测试跳过（环境性因素），避免本地开发循环恒红。
    """
    try:
        capture_fullscreen()
    except Exception as exc:  # mss.exception.ScreenShotError 等
        pytest.skip(f"真实屏幕不可用（环境性因素，跳过真实截图测试）：{exc!r}")
    return True


def test_capture_fullscreen_returns_image(real_screen):
    img = capture_fullscreen()
    assert isinstance(img, Image.Image)
    assert img.width > 0 and img.height > 0


def test_capture_fullscreen_multiple_monitors(real_screen):
    img = capture_fullscreen()
    assert img.size == capture_virtual_screen_size()


def test_capture_region(real_screen):
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


def test_copy_text_to_clipboard_sets_unicode_text():
    from unittest.mock import patch
    with patch("win32clipboard.OpenClipboard"), \
         patch("win32clipboard.EmptyClipboard"), \
         patch("win32clipboard.SetClipboardData") as scd, \
         patch("win32clipboard.CloseClipboard"):
        assert copy_text_to_clipboard("你好 hello") is True
    scd.assert_called_once_with(13, "你好 hello")  # CF_UNICODETEXT


def test_copy_text_to_clipboard_returns_false_on_error():
    from unittest.mock import patch
    with patch("win32clipboard.OpenClipboard", side_effect=OSError("clipboard busy")):
        assert copy_text_to_clipboard("x") is False


def test_get_desktop_path_from_shell32(monkeypatch):
    import ctypes
    def fake_shget(hwnd, csidl, token, flags, buf):
        buf.value = "C:\\Users\\w\\桌面"
        return 0
    monkeypatch.setattr(ctypes.windll.shell32, "SHGetFolderPathW", fake_shget)
    assert get_desktop_path() == "C:\\Users\\w\\桌面"


def test_get_desktop_path_fallback(monkeypatch):
    import ctypes
    monkeypatch.setattr(ctypes.windll.shell32, "SHGetFolderPathW", lambda *a: 1)  # E_FAIL
    assert get_desktop_path() == os.path.join(os.path.expanduser("~"), "Desktop")


def test_default_save_path_uses_desktop(monkeypatch):
    import capture
    monkeypatch.setattr(capture, "get_desktop_path", lambda: "D:\\桌面")
    p = default_save_path()
    assert p.startswith("D:\\桌面\\截图_")


def test_save_image_avoids_overwrite(tmp_path):
    img = Image.new("RGB", (2, 2), (255, 0, 0))
    path = tmp_path / "截图_20260101_120000.png"
    path.write_bytes(b"ORIGINAL")
    p1 = save_image(img, str(path))
    assert p1 == str(tmp_path / "截图_20260101_120000_1.png")
    assert path.read_bytes() == b"ORIGINAL"  # 原文件未被覆盖
    p2 = save_image(img, str(path))
    assert p2 == str(tmp_path / "截图_20260101_120000_2.png")
    # 无冲突时直接保存原路径
    p3 = save_image(img, str(tmp_path / "free.png"))
    assert p3 == str(tmp_path / "free.png")
