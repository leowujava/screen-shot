import os

import pytest
from PIL import Image
from capture import (
    capture_fullscreen, capture_region, capture_virtual_screen_size,
    _build_dib, copy_text_to_clipboard, copy_to_clipboard, get_desktop_path,
    default_save_path, save_image,
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


def test_copy_to_clipboard_sets_only_dib():
    """图像剪贴板只写 CF_DIB：CF_BITMAP 由 Windows 从 CF_DIB 合成。

    回归：曾把 DIB 字节直接写入 CF_BITMAP，得到无效位图句柄，优先读取
    CF_BITMAP 的应用（部分 Office/聊天工具）会粘贴失败。
    """
    from unittest.mock import patch
    import win32con

    with patch("win32clipboard.OpenClipboard"), \
         patch("win32clipboard.EmptyClipboard"), \
         patch("win32clipboard.SetClipboardData") as scd, \
         patch("win32clipboard.CloseClipboard"):
        copy_to_clipboard(Image.new("RGB", (2, 2), (255, 0, 0)))

    formats = [c.args[0] for c in scd.call_args_list]
    assert formats == [win32con.CF_DIB]


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


def test_save_image_concurrent_no_overwrite(tmp_path):
    """并发保存到同一路径：各得独立文件，互不覆盖。

    回归：旧实现「exists 检查 → 写入」分两步，并发时可能选中
    同一文件名互相覆盖；现用 O_CREAT|O_EXCL 原子占名。
    """
    import threading
    path = str(tmp_path / "shot.png")
    n_workers = 8
    barrier = threading.Barrier(n_workers)
    results = []
    lock = threading.Lock()

    def worker(i):
        img = Image.new("RGB", (4, 4), (i * 10, 0, 0))
        barrier.wait()
        p = save_image(img, path)
        with lock:
            results.append((i, p))

    threads = [threading.Thread(target=worker, args=(i,))
               for i in range(n_workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len({p for _, p in results}) == n_workers, "并发保存出现重复路径（覆盖）"
    for i, p in results:
        # 内容仍是各自写入的颜色（未被其他线程覆盖）
        assert Image.open(p).getpixel((0, 0))[0] == i * 10


def test_save_image_failure_leaves_no_empty_file(tmp_path, monkeypatch):
    """写入失败时清理已占位的空文件，异常照常抛出。"""
    img = Image.new("RGB", (2, 2))
    path = str(tmp_path / "bad.png")

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(img, "save", boom)
    with pytest.raises(OSError, match="disk full"):
        save_image(img, path)
    assert not os.path.exists(path)
