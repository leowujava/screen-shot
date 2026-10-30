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


def default_save_path():
    """默认保存路径：桌面/截图_时间戳.png"""
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(desktop, f"截图_{ts}.png")


def save_image(img, path=None):
    """保存图片，返回实际保存路径。失败抛异常。"""
    path = path or default_save_path()
    img.save(path)
    return path


def _build_dib(img):
    """把 PIL Image 编码为 32 位 BGRA、bottom-up 的 DIB 字节串（BITMAPINFOHEADER + 像素）。

    biHeight 为正表示 bottom-up（首行是图像最底部）。Pillow 的 tobytes 默认给出
    top-down 行序，所以这里逐行翻转成 bottom-up。
    """
    import struct

    if img.mode != "RGB":
        img = img.convert("RGB")
    w, h = img.size

    # 32 位 BGRX（BGRA）top-down 像素
    pixels = img.tobytes("raw", "BGRX")

    # 把 top-down 行序翻转成 bottom-up（每行 w*4 字节）
    row = w * 4
    rows = [pixels[i * row:(i + 1) * row] for i in range(h)]
    rows.reverse()
    pixels_bu = b"".join(rows)

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


def copy_to_clipboard(img):
    """把图像复制到系统剪贴板（32 位 DIB，可直接 Ctrl+V 粘贴）。

    手工构造 DIB 字节串后交给 win32clipboard 以 CF_BITMAP / CF_DIB 写入剪贴板，
    由 pywin32 负责剪贴板内存的生命周期，兼容 Word / Paint / 浏览器等。
    """
    import win32clipboard
    import win32con

    dib = _build_dib(img)

    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_BITMAP, dib)
        win32clipboard.SetClipboardData(win32con.CF_DIB, dib)
    finally:
        win32clipboard.CloseClipboard()
