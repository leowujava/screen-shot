import ctypes
import os
from datetime import datetime
from PIL import Image
import mss


def capture_virtual_screen_size():
    """返回虚拟桌面（所有显示器合并）的 (width, height)。"""
    with mss.MSS() as sct:
        mon = sct.monitors[0]
        return mon["width"], mon["height"]


def capture_fullscreen():
    """截取全部虚拟桌面，返回 PIL Image。"""
    with mss.MSS() as sct:
        mon = sct.monitors[0]
        raw = sct.grab(mon)
        return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


def capture_region(left, top, width, height):
    """截取虚拟桌面内指定区域，返回 PIL Image。"""
    with mss.MSS() as sct:
        raw = sct.grab({"left": left, "top": top, "width": width, "height": height})
        return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


def get_desktop_path():
    """解析真实桌面路径（中文系统返回 桌面 目录）；失败回退 ~\\Desktop。"""
    import ctypes
    buf = ctypes.create_unicode_buffer(260)
    try:
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0, None, 0x20, buf) == 0:
            return buf.value
    except Exception:
        pass
    return os.path.join(os.path.expanduser("~"), "Desktop")


def default_save_path():
    """默认保存路径：桌面/截图_时间戳.png"""
    desktop = get_desktop_path()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(desktop, f"截图_{ts}.png")


def save_image(img, path=None):
    """保存图片，返回实际保存路径。目标已存在时追加 _1、_2... 后缀防覆盖。失败抛异常。

    用 O_CREAT|O_EXCL 原子地占住文件名再写入：并发保存不会因
    「exists 检查 → 写入」之间的竞态而互相覆盖。
    """
    path = path or default_save_path()
    base, ext = os.path.splitext(path)
    candidate = path
    n = 0
    while True:
        try:
            fd = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            n += 1
            candidate = f"{base}_{n}{ext}"
            continue
        os.close(fd)
        try:
            img.save(candidate)
        except Exception:
            # 写入失败不留空文件（best-effort 清理）
            try:
                os.remove(candidate)
            except OSError:
                pass
            raise
        return candidate


def _build_dib(img):
    """把 PIL Image 编码为 32 位 BGRA、bottom-up 的 DIB 字节串（BITMAPINFOHEADER + 像素）。

    biHeight 为正表示 bottom-up（首行是图像最底部）。Pillow 的 tobytes 默认给出
    top-down 行序，所以这里用 transpose 翻转成 bottom-up。
    """
    import struct

    if img.mode != "RGB":
        img = img.convert("RGB")
    w, h = img.size

    # C 级整块翻转（bottom-up），替代原 Python 逐行切片循环
    pixels_bu = img.transpose(Image.FLIP_TOP_BOTTOM).tobytes("raw", "BGRX")

    # BITMAPINFOHEADER（40 字节）
    header = struct.pack(
        "<IiiHHIIiiII",
        40,
        w,
        h,          # 正数 = bottom-up
        1,          # biPlanes
        32,         # biBitCount
        0,          # biCompression = BI_RGB
        len(pixels_bu),
        0,          # biXPelsPerMeter
        0,          # biYPelsPerMeter
        0,          # biClrUsed
        0,          # biClrImportant
    )

    return header + pixels_bu


# 剪贴板打开重试：其他程序（Office / 剪贴板管理器 / 远程桌面）可能短暂
# 占用剪贴板，瞬时占用不应视为永久失败。
_CLIPBOARD_RETRIES = 8
_CLIPBOARD_RETRY_DELAY = 0.05  # 秒，全部失败前总等待约 0.35s


def _open_clipboard_with_retry():
    """打开剪贴板；失败短间隔重试，全部失败抛最后一次异常。"""
    import time
    import win32clipboard
    last_exc = None
    for attempt in range(_CLIPBOARD_RETRIES):
        try:
            win32clipboard.OpenClipboard()
            return
        except Exception as exc:
            last_exc = exc
            if attempt < _CLIPBOARD_RETRIES - 1:
                time.sleep(_CLIPBOARD_RETRY_DELAY)
    raise last_exc


def copy_to_clipboard(img):
    """把图像复制到系统剪贴板（32 位 DIB，可直接 Ctrl+V 粘贴）。

    手工构造 DIB 字节串后交给 win32clipboard 以 CF_DIB 写入剪贴板，
    由 pywin32 负责剪贴板内存的生命周期，兼容 Word / Paint / 浏览器等。

    只写 CF_DIB：Windows 会在应用请求 CF_BITMAP 时由 CF_DIB 合成
    合法的 HBITMAP。此前同时把 DIB 字节写入 CF_BITMAP，拿到的是无效
    位图句柄，部分优先读取 CF_BITMAP 的应用会粘贴失败。
    """
    import win32clipboard
    import win32con

    dib = _build_dib(img)

    _open_clipboard_with_retry()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_DIB, dib)
    finally:
        win32clipboard.CloseClipboard()


def copy_text_to_clipboard(text):
    """把文本复制到系统剪贴板（CF_UNICODETEXT）。成功返回 True，失败返回 False。"""
    import win32clipboard
    import win32con
    try:
        _open_clipboard_with_retry()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
        finally:
            win32clipboard.CloseClipboard()
        return True
    except Exception:
        return False
