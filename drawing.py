"""共享绘制函数（纯 PIL，无 Tk 依赖）。

供 editor.py 和 selector.py 使用。
"""
import math
import sys
from PIL import Image, ImageDraw, ImageFilter

# ---------------------------------------------------------------------------
# OCR（识别文字）
# ---------------------------------------------------------------------------

_OCR_ENGINE = None
_OCR_ENGINE_READY = False


def get_ocr_engine():
    """懒加载 OCR 引擎（首次调用时初始化）。失败返回 None。

    优先使用 rapidocr（PP-OCRv6，中英混合）；回退 rapidocr-onnxruntime（仅中文）。
    """
    global _OCR_ENGINE, _OCR_ENGINE_READY
    if _OCR_ENGINE_READY:
        return _OCR_ENGINE
    try:
        from rapidocr import RapidOCR
        _OCR_ENGINE = RapidOCR()
        _OCR_ENGINE_READY = True
        return _OCR_ENGINE
    except Exception:
        try:
            from rapidocr_onnxruntime import RapidOCR
            _OCR_ENGINE = RapidOCR()
            _OCR_ENGINE_READY = True
            return _OCR_ENGINE
        except Exception:
            return None


def ocr_result_to_text(result):
    """把 OCR 结果拼成按阅读顺序的文本（先上后下、先左后右，换行连接）。

    兼容两种返回格式：
    - rapidocr (v3) 的 RapidOCROutput：.boxes (n×4×2 ndarray) 与 .txts 一一对应
    - rapidocr-onnxruntime 的 list：[[box, text, conf], ...]
    result 为 None / 空时返回空字符串。
    """
    if result is None:
        return ""

    # 格式一：rapidocr v3 的 RapidOCROutput（有 .boxes / .txts 属性）
    boxes = getattr(result, "boxes", None)
    txts = getattr(result, "txts", None)
    if boxes is not None and txts is not None:
        if len(txts) == 0:
            return ""
        idx = sorted(range(len(txts)),
                     key=lambda i: (round(boxes[i][0][1] / 8), boxes[i][0][0]))
        return "\n".join(txts[i] for i in idx)

    # 格式二：rapidocr-onnxruntime 的 list [[box, text, conf], ...]
    # 或 (list, times)：([box, text, conf], ...), (..., ...,)
    if isinstance(result, (list, tuple)):
        if len(result) == 0:
            return ""
        if len(result) == 2 and isinstance(result[0], (list, tuple)) and isinstance(result[1], (list, tuple)):
            result = result[0]
            if len(result) == 0:
                return ""
        idx = sorted(range(len(result)),
                     key=lambda i: (round(result[i][0][0][1] / 8), result[i][0][0][0]))
        return "\n".join(result[i][1] for i in idx)

    return ""


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

    def __len__(self):
        return len(self._stack)


def alt_pressed(event=None):
    """事件发生时是否真正按住 Alt（用于约束正圆/正方形）。

    Windows 下用 GetAsyncKeyState 查询（Tk 事件 state 在 Alt 释放后可能
    仍带标志位，不可靠）；其余平台回退到 event.state。event 为 None 时 False。
    """
    if sys.platform.startswith("win"):
        try:
            import ctypes
            VK_MENU = 0x12  # Alt
            return bool(ctypes.windll.user32.GetAsyncKeyState(VK_MENU) & 0x8000)
        except Exception:
            pass
    state = getattr(event, "state", 0) or 0
    return bool(state & 0x20000)


def mosaic(img, left, top, right, bottom, block=24):
    """对 img 的 [left,top,right,bottom] 区域做马赛克，返回新图。

    选区无效（宽或高为 0、越界后无面积）时原样返回副本。
    先轻微高斯模糊，再按 block 倍下采样（BILINEAR 平均像素），
    最后用 NEAREST 放大回原尺寸——像素块粗大、内容细节不可辨认。
    block 为马赛克块边长（像素），默认 24。
    """
    result = img.copy()
    left, top = max(0, left), max(0, top)
    right = min(right, result.width)
    bottom = min(bottom, result.height)
    if right <= left or bottom <= top:
        return result
    region = result.crop((left, top, right, bottom))
    w, h = region.size
    block = max(1, int(block))
    region = region.filter(ImageFilter.GaussianBlur(radius=1.2))
    small = region.resize((max(1, w // block), max(1, h // block)), Image.BILINEAR)
    big = small.resize((w, h), Image.NEAREST)
    result.paste(big, (left, top))
    return result


def _supersample(img, draw_fn, scale=4):
    """超采样抗锯齿：在 scale 倍大的透明层上绘制，再缩回原尺寸。

    用 LANCZOS 缩小时做面积平均，边缘产生平滑的半透明过渡（灰度渐晕），
    消除锯齿。draw_fn(d, s) 接收放大后的 Draw 对象和缩放系数，
    负责在放大图上完成绘制。返回与原图同尺寸/模式的新图。
    """
    layer = Image.new("RGBA", (img.width * scale, img.height * scale), (0, 0, 0, 0))
    draw_fn(ImageDraw.Draw(layer), scale)
    small = layer.resize((img.width, img.height), Image.LANCZOS)
    out = img.convert("RGBA")
    out.alpha_composite(small)
    return out.convert(img.mode)


def _supersample_inplace(img, draw_fn, scale=4):
    """超采样绘制后把结果写回 img（保持原对象，供逐段画笔累积）。"""
    result = _supersample(img, draw_fn, scale)
    img.paste(result, (0, 0))
    return img


def _supersample_local(img, x0, y0, x1, y1, draw_fn, scale=4):
    """局部超采样：只在 [x0,y0,x1,y1] 包围盒范围内做 scale 倍超采样。

    draw_fn(d, s) 接收局部层 Draw 对象与缩放系数，以局部层坐标
    （图像坐标减去 (x0, y0) 原点，再 × s）绘制。包围盒可越界，
    越界部分绘制后被丢弃。把结果合成回 img（保持原对象）。
    """
    W, H = img.size
    bw, bh = x1 - x0, y1 - y0
    if bw <= 0 or bh <= 0:
        return img
    layer = Image.new("RGBA", (bw * scale, bh * scale), (0, 0, 0, 0))
    draw_fn(ImageDraw.Draw(layer), scale)
    small = layer.resize((bw, bh), Image.LANCZOS)
    # small 覆盖的图像实际区域（与图像求交）
    cx0, cy0 = max(0, x0), max(0, y0)
    cx1, cy1 = min(W, x1), min(H, y1)
    if cx1 <= cx0 or cy1 <= cy0:
        return img
    crop = small.crop((cx0 - x0, cy0 - y0, cx1 - x0, cy1 - y0))
    patch = img.crop((cx0, cy0, cx1, cy1)).convert("RGBA")
    patch.alpha_composite(crop)
    img.paste(patch.convert(img.mode), (cx0, cy0))
    return img


def draw_arrow(img, start, end, color, width):
    """在 img 上画箭头（线条 + 三角箭头头），4x 超采样抗锯齿（原地修改）。

    start/end 为 (x, y) 元组，color 为颜色，width 为线宽。
    """
    x1, y1 = start
    x2, y2 = end
    length = math.hypot(x2 - x1, y2 - y1)
    # 箭头头：尖端在 end 处、朝线条方向；底边两点在 end 后方。
    # 头长 = max(16, 3.5x 线宽)（仿参考图的大三角头），
    # 开口角 ±0.55 rad（~31.5°，宽而钝的实心三角）
    head = max(16.0, 3.5 * width) if length >= 1 else 0
    ang = math.atan2(y2 - y1, x2 - x1) if length >= 1 else 0
    p1 = (x2 - head * math.cos(ang - 0.55), y2 - head * math.sin(ang - 0.55)) if length >= 1 else (x2, y2)
    p2 = (x2 - head * math.cos(ang + 0.55), y2 - head * math.sin(ang + 0.55)) if length >= 1 else (x2, y2)

    def draw(d, s):
        d.line([(x1 * s, y1 * s), (x2 * s, y2 * s)], fill=color, width=max(1, int(width * s)))
        if length >= 1:
            d.polygon([(x2 * s, y2 * s), (p1[0] * s, p1[1] * s), (p2[0] * s, p2[1] * s)],
                      fill=color)

    _supersample_inplace(img, draw)
    return img


def draw_rect(img, box, color, width):
    """在 img 上画 outline 矩形（4x 超采样抗锯齿，原地修改）。box 已归一化 (x0, y0, x1, y1)。"""
    def draw(d, s):
        d.rectangle([v * s for v in box], outline=color, width=max(1, int(width * s)))

    _supersample_inplace(img, draw)
    return img


def draw_ellipse(img, box, color, width):
    """在 img 上画 outline 椭圆（4x 超采样抗锯齿，原地修改）。box 已归一化 (x0, y0, x1, y1)。"""
    def draw(d, s):
        d.ellipse([v * s for v in box], outline=color, width=max(1, int(width * s)))

    _supersample_inplace(img, draw)
    return img


def draw_brush(img, points, color, width):
    """在 img 上画折线画笔（4x 超采样抗锯齿，圆头线帽，局部作用域）。points 为 [(x, y), ...]。"""
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    margin = max(2 * width, 8)  # ≥ LANCZOS 重采样窗口(3px) 与线宽，保证与整图超采样一致
    x0, y0 = int(min(xs)) - margin, int(min(ys)) - margin
    x1, y1 = int(max(xs)) + margin, int(max(ys)) + margin

    def draw(d, s):
        if len(points) == 1:
            px, py = points[0]
            d.ellipse([(px - x0 - width / 2) * s, (py - y0 - width / 2) * s,
                       (px - x0 + width / 2) * s, (py - y0 + width / 2) * s], fill=color)
        else:
            d.line([((px - x0) * s, (py - y0) * s) for px, py in points], fill=color,
                   width=max(1, int(width * s)), joint="curve")

    return _supersample_local(img, x0, y0, x1, y1, draw)


def draw_text(img, pos, text, color, font_size):
    """在 img 上画文字。pos 为 (x, y)，font_size 为 int。"""
    d = ImageDraw.Draw(img)
    try:
        from PIL import ImageFont
        font = ImageFont.truetype("arial.ttf", font_size)
    except Exception:
        font = ImageFont.load_default()
    d.text(pos, text, fill=color, font=font)
