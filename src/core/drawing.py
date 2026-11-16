"""共享绘制函数（纯 PIL）。

供 editor.py 和 selector.py 使用。
"""
import math
import sys
import threading
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ---------------------------------------------------------------------------
# OCR（识别文字）
# ---------------------------------------------------------------------------

_OCR_ENGINE = None
_OCR_ENGINE_READY = False
# OCR 引擎在多个工作线程里并发触发时只初始化一次（模型加载很慢）
_OCR_LOCK = threading.Lock()


def get_ocr_engine():
    """懒加载 OCR 引擎（首次调用时初始化）。失败返回 None。

    优先使用 rapidocr（PP-OCRv6，中英混合）；回退 rapidocr-onnxruntime（仅中文）。
    线程安全：OCR 点击可能并发，锁内二次检查避免重复加载模型。
    """
    global _OCR_ENGINE, _OCR_ENGINE_READY
    if _OCR_ENGINE_READY:
        return _OCR_ENGINE
    with _OCR_LOCK:
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


# ---------------------------------------------------------------------------
# 字体（中文字体链）
# ---------------------------------------------------------------------------

# 按顺序尝试加载的字体文件名（中文系统优先微软雅黑，其次黑体，回退 Arial）
_FONT_CANDIDATES = ("msyh.ttc", "msyh.ttf", "simhei.ttf", "arial.ttf")
_font_cache = {}


def _load_font(size):
    """按中文字体链加载 TrueType 字体，缓存 (文件名, size)。

    逐个尝试 _FONT_CANDIDATES，命中即用；全部失败（或系统无这些字体）时
    回退 ImageFont.load_default()，保证不抛异常。
    """
    for name in _FONT_CANDIDATES:
        cached_key = (name, size)
        font = _font_cache.get(cached_key)
        if font is not None:
            return font
        try:
            font = ImageFont.truetype(name, size)
        except Exception:
            continue
        _font_cache[cached_key] = font
        return font
    # 全部失败：回退 Pillow 默认字体（同样按 size 缓存）
    font = _font_cache.get(("default", size))
    if font is None:
        try:
            font = ImageFont.load_default(size=size)
        except TypeError:  # Pillow < 10.1 不支持 size 参数
            font = ImageFont.load_default()
        _font_cache[("default", size)] = font
    return font


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

    def map(self, fn):
        """用 fn 变换每个快照（如选区移动后把快照重新定基到新位置）。"""
        self._stack = [fn(item) for item in self._stack]

    def __len__(self):
        return len(self._stack)


def alt_pressed(event=None):
    """事件发生时是否真正按住 Alt（用于约束正圆/正方形）。

    Windows 下用 GetAsyncKeyState 查询实时按键状态（事件携带的修饰键
    标志可能滞后，不可靠）；其余平台回退到 Qt 事件的 modifiers()。
    event 为 None 时 False。
    """
    if sys.platform.startswith("win"):
        try:
            import ctypes
            VK_MENU = 0x12  # Alt
            return bool(ctypes.windll.user32.GetAsyncKeyState(VK_MENU) & 0x8000)
        except Exception:
            pass
    # Qt.AltModifier = 0x02000000。鸭子类型取 modifiers()，
    # 不在纯 PIL 模块里引入 Qt 依赖。
    modifiers = getattr(event, "modifiers", None)
    if callable(modifiers):
        try:
            return bool(modifiers() & 0x02000000)
        except Exception:
            pass
    return False


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


# 箭头几何常量（参照参考图实测比例标定）。
# draw_arrow 与选区预览共用，保证预览与最终渲染几何一致。
ARROW_HALF_ANGLE = 0.48        # 头半顶角（rad ≈ 27.5°；实测 27°~30°，底宽 ≈ 0.92×头长）
ARROW_TAPER = 0.05            # 杆向头端增宽系数：Δ = ARROW_TAPER × 箭长（实测 0.052~0.054）
ARROW_HEAD_RATIO = 2.4        # 头长 = ARROW_HEAD_RATIO × 头端杆宽（实测 2.2~2.6）


def arrow_geometry(start, end, width):
    """计算箭头几何：锥形杆四边形 + 实心三角箭头。

    返回 (shaft, head)，均为 (x, y) 点列表：
    - shaft：4 点多边形，前两点为尾边（宽 = width）、后两点为头端边（更宽）。
      杆从尾端向头端线性增宽，增宽量 Δ = max(width, ARROW_TAPER×箭长)
      （参考图实测：尾端细、向头端逐渐变宽）。
    - head：3 点实心三角，[尖端(=end), 底边点1, 底边点2]；
      头长 = max(16, ARROW_HEAD_RATIO×头端杆宽)，且不超过箭长的一半量级
      （底边不越过起点、短箭头不成"全头"）。
    - 头端杆宽收敛到头底宽以内，线头不会穿出三角。
    - length < 1（太短）时返回 (None, None)，由调用方画点。
    """
    x1, y1 = start
    x2, y2 = end
    length = math.hypot(x2 - x1, y2 - y1)
    if length < 1:
        return None, None
    ang = math.atan2(y2 - y1, x2 - x1)
    ux, uy = math.cos(ang), math.sin(ang)
    vx, vy = -uy, ux  # 垂直于轴线
    # 头长（受箭长约束）
    head = max(16.0, ARROW_HEAD_RATIO * (width + max(width, ARROW_TAPER * length)))
    head = min(head, 0.5 * length / math.cos(ARROW_HALF_ANGLE))
    base = head * math.cos(ARROW_HALF_ANGLE)          # 底面到尖端的轴向距离
    half = head * math.sin(ARROW_HALF_ANGLE)          # 底边半宽
    ex, ey = x2 - base * ux, y2 - base * uy           # 杆端点（底面中点）
    # 杆宽：尾端 = width，向头端增宽；极端（短线+超粗）时收敛到头底宽以内
    w_base = width + max(width, ARROW_TAPER * length)
    w_base = min(w_base, 2 * half)
    w_tail = min(width, w_base)
    shaft = [(x1 - w_tail / 2 * vx, y1 - w_tail / 2 * vy),
             (x1 + w_tail / 2 * vx, y1 + w_tail / 2 * vy),
             (ex + w_base / 2 * vx, ey + w_base / 2 * vy),
             (ex - w_base / 2 * vx, ey - w_base / 2 * vy)]
    head_pts = [(x2, y2),
                (ex + half * vx, ey + half * vy),
                (ex - half * vx, ey - half * vy)]
    return shaft, head_pts


def draw_arrow(img, start, end, color, width):
    """在 img 上画箭头（锥形杆 + 实心三角箭头头），4x 超采样抗锯齿（原地修改）。

    start/end 为 (x, y) 元组，color 为颜色，width 为线宽（杆尾端宽度）。
    几何见 arrow_geometry：杆从尾端向头端线性增宽，线头不会穿出三角。
    """
    shaft, head_pts = arrow_geometry(start, end, width)
    if shaft is None:  # 太短：画一个点
        def draw(d, s):
            d.line([(start[0] * s, start[1] * s), (end[0] * s, end[1] * s)],
                   fill=color, width=max(1, int(width * s)))
        _supersample_inplace(img, draw)
        return img

    def draw(d, s):
        d.polygon([(x * s, y * s) for x, y in shaft], fill=color)
        d.polygon([(x * s, y * s) for x, y in head_pts], fill=color)

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
    """在 img 上画文字。pos 为 (x, y)，font_size 为 int。

    字体走中文字体链（见 _load_font），中文标注可正常渲染为真实字形。
    """
    d = ImageDraw.Draw(img)
    d.text(pos, text, fill=color, font=_load_font(font_size))
