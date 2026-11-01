"""共享绘制函数（纯 PIL，无 Tk 依赖）。

供 editor.py 和 selector.py 使用。
"""
import math
from PIL import Image, ImageDraw


class UndoStack:
    def __init__(self, maxlen=10):
        self.maxlen = maxlen
        self._stack = []

    def push(self, item):
        self._stack.append(item)
        if len(self._stack) > self.maxlen:
            self._stack.pop(0)

    def pop(self):
        if self._stack:
            return self._stack.pop()
        return None

    def clear(self):
        self._stack.clear()


def mosaic(img, left, top, right, bottom, block=8):
    """对 img 的 [left,top,right,bottom] 区域做马赛克，返回新图。

    选区无效（宽或高为 0、越界后无面积）时原样返回副本。
    缩放使用 BILINEAR（下采样时平均像素，产生灰度过渡），
    再用 NEAREST 放大（像素块清晰可见），实现"马赛克"效果。
    """
    result = img.copy()
    left, top = max(0, left), max(0, top)
    right = min(right, result.width)
    bottom = min(bottom, result.height)
    if right <= left or bottom <= top:
        return result
    region = result.crop((left, top, right, bottom))
    w, h = region.size
    small = region.resize((max(1, w // block), max(1, h // block)), Image.BILINEAR)
    big = small.resize((w, h), Image.NEAREST)
    result.paste(big, (left, top))
    return result


def draw_arrow(img, start, end, color, width):
    """在 img 上画箭头（线条 + 三角箭头头）。

    start/end 为 (x, y) 元组，color 为颜色，width 为线宽。
    """
    d = ImageDraw.Draw(img)
    x1, y1 = start
    x2, y2 = end
    d.line([start, end], fill=color, width=width)
    length = math.hypot(x2 - x1, y2 - y1)
    if length < 1:
        return
    angle = math.atan2(y2 - y1, x2 - x1)
    head = 12
    a1 = angle + math.pi * 0.8
    a2 = angle - math.pi * 0.8
    p1 = (x2 + head * math.cos(a1), y2 + head * math.sin(a1))
    p2 = (x2 + head * math.cos(a2), y2 + head * math.sin(a2))
    d.polygon([end, p1, p2], fill=color)


def draw_rect(img, box, color, width):
    """在 img 上画 outline 矩形。box 已归一化 (x0, y0, x1, y1)。"""
    d = ImageDraw.Draw(img)
    d.rectangle(box, outline=color, width=width)


def draw_ellipse(img, box, color, width):
    """在 img 上画 outline 椭圆。box 已归一化 (x0, y0, x1, y1)。"""
    d = ImageDraw.Draw(img)
    d.ellipse(box, outline=color, width=width)


def draw_brush(img, points, color, width):
    """在 img 上画折线画笔。points 为 [(x, y), ...]。"""
    d = ImageDraw.Draw(img)
    if len(points) == 1:
        d.point(points[0], fill=color)
    else:
        d.line(points, fill=color, width=width)


def draw_text(img, pos, text, color, font_size):
    """在 img 上画文字。pos 为 (x, y)，font_size 为 int。"""
    d = ImageDraw.Draw(img)
    try:
        from PIL import ImageFont
        font = ImageFont.truetype("arial.ttf", font_size)
    except Exception:
        font = ImageFont.load_default()
    d.text(pos, text, fill=color, font=font)
