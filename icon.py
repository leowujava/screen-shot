"""SVG 图标 → ImageTk.PhotoImage 渲染（支持重着色 + 缩放 + 缓存）。

渲染后端：svglib + reportlab + rlPyCairo（纯 Python，无需系统 cairo 库）。
懒加载：顶层不 import svglib/reportlab，首次 render() 时才 import。
renderer 不可用时返回 1×1 透明 PhotoImage（不抛异常），保证 UI 不崩。
"""
import io
import os
import tkinter as tk
from PIL import Image, ImageTk

from theme import ICON_SIZE

_ROOT = os.path.dirname(os.path.abspath(__file__))
_ICONS_DIR = os.path.join(_ROOT, "assets", "icons")

_cache = {}                  # (name, color, size) -> PhotoImage
_RENDERER_AVAILABLE = True   # 首次 render 时探测
_renderer_failed = False     # 已探测失败，不再重试


def _icon_path(name: str) -> str:
    return os.path.join(_ICONS_DIR, f"{name}.svg")


def _hex_to_rgb(color: str) -> tuple:
    v = color.lstrip("#")
    if len(v) == 3:
        v = "".join(ch * 2 for ch in v)
    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)


def _svg_to_rgba(svg_path: str, color: str, size: int) -> Image.Image:
    """svglib 渲染 SVG → ARGB32 → PIL RGBA，按 color 重着色（保留 alpha）。"""
    from svglib.svglib import svg2rlg
    from reportlab.graphics import renderPM

    with open(svg_path, "rb") as fh:
        svg_bytes = fh.read()
    drawing = svg2rlg(io.BytesIO(svg_bytes))
    drawing.width = size
    drawing.height = size
    canvas = renderPM.drawToPMCanvas(drawing, dpi=72, bg=0x00000000,
                                     backendFmt="RGBA")
    w, h = canvas._gs.width, canvas._gs.height
    pix = canvas._gs.pixBuf
    img = Image.new("RGBA", (w, h))
    img.frombytes(pix, "raw", "ARGB")
    if img.size != (size, size):
        img = img.resize((size, size), Image.LANCZOS)
    # 重着色：保留 alpha，RGB 替换为 color
    c = _hex_to_rgb(color)
    out = Image.new("RGBA", img.size, (*c, 255))
    out.putalpha(img.getchannel("A"))
    return out


def _blank_photo(master, size: int) -> "tk.PhotoImage":
    """size×size 透明占位图（renderer 不可用或 svg 缺失时）。"""
    img = Image.new("RGBA", (max(1, size), max(1, size)), (0, 0, 0, 0))
    return ImageTk.PhotoImage(img, master=master)


def render(name: str, color: str, size: int = ICON_SIZE,
           master=None) -> "tk.PhotoImage":
    """渲染 assets/icons/<name>.svg 为 PhotoImage，按 color 着色，size×size。

    - 同一 (name, color, size) 只渲染一次，后续命中缓存。
    - 图标文件不存在 / renderer 不可用：返回 size×size 透明 PhotoImage（不抛异常）。
    - master: 可选的 Tk 根/窗口，PhotoImage 绑定到它（避免被 GC）。
    """
    global _RENDERER_AVAILABLE, _renderer_failed
    key = (name, color, size)
    if key in _cache:
        return _cache[key]
    photo = None
    svg_path = _icon_path(name)
    if os.path.exists(svg_path) and not _renderer_failed:
        try:
            img = _svg_to_rgba(svg_path, color, size)
            photo = ImageTk.PhotoImage(img, master=master)
        except Exception:
            # renderer 不可用（import 失败 / 渲染异常）：标记失败，后续快速退化
            _renderer_failed = True
            _RENDERER_AVAILABLE = False
    if photo is None:
        photo = _blank_photo(master, size)
    _cache[key] = photo
    return photo


def _reset_cache():
    """清空缓存（测试用）。"""
    global _renderer_failed, _RENDERER_AVAILABLE
    _cache.clear()
    _renderer_failed = False
    _RENDERER_AVAILABLE = True
