import os
import sys

import pytest
from PIL import Image, ImageDraw
from drawing import (
    draw_arrow, draw_rect, draw_ellipse, draw_brush, draw_text, mosaic, UndoStack,
)


def _blank(w=100, h=100, color=(255, 255, 255)):
    return Image.new("RGB", (w, h), color)


def _close(a, b, tol=40):
    """颜色分量接近判定（抗锯齿后边缘存在灰度过渡，允许容差）。"""
    return all(abs(x - y) <= tol for x, y in zip(a, b))



def test_draw_arrow_draws_line_and_head():
    img = _blank()
    draw_arrow(img, (10, 10), (90, 90), "#ff0000", width=3)
    # 中心点应为红色（箭头线条经过）
    assert _close(img.getpixel((50, 50)), (255, 0, 0), 25)
    # 箭头尖端附近也应有红色
    assert _close(img.getpixel((88, 88)), (255, 0, 0), 25)
    # 起点和终点之外的区域应保持白色
    assert img.getpixel((5, 5)) == (255, 255, 255)
    assert img.getpixel((95, 5)) == (255, 255, 255)


def test_draw_arrow_no_line_ears_at_tip():
    """杆只延伸到箭头头底边，尖端两侧不应有线头穿出的"耳"。

    旧实现把杆画到尖端：杆宽宽于尖端附近的三角开口，线头在尖端两侧
    各凸出一小块（如右向箭头尖端上方 1px 处出现浅红像素）。
    """
    img = _blank(100, 100)
    draw_arrow(img, (10, 50), (90, 50), "#ff0000", width=3)
    # 尖端前方 1px、轴线上方 ~1.5px：三角开口之外（允许抗锯齿渐晕）
    for px in ((89, 48), (89, 52)):
        assert max(abs(c - 255) for c in img.getpixel(px)) <= 12, px
    # 三角本体仍应实心红色
    assert _close(img.getpixel((85, 50)), (255, 0, 0), 25)
    assert _close(img.getpixel((80, 46)), (255, 0, 0), 25)
    assert _close(img.getpixel((80, 54)), (255, 0, 0), 25)


def test_arrow_geometry_tapered_shaft_and_head():
    """锥形杆四边形 + 实心三角：头端比尾端宽，头底边不越过起点，尖端 = end。"""
    from drawing import arrow_geometry

    # 常规长度（右向）：尾边宽 = 线宽，头端边更宽
    shaft, head = arrow_geometry((0, 0), (100, 0), 3)
    assert abs(shaft[0][1] - shaft[1][1]) == pytest.approx(3.0)   # 尾宽 = 线宽
    assert abs(shaft[2][1] - shaft[3][1]) > 3.0                   # 头端增宽
    assert shaft[2][0] < 100 and shaft[2][0] >= 0                 # 杆端在起点与尖端之间
    # 三角头：尖端 = end，底边两点在尖端后方、关于轴对称
    assert head[0] == (100, 0)
    assert head[1][0] < 100 and head[2][0] < 100
    assert abs(head[1][0] - head[2][0]) < 1e-9
    assert abs(head[1][1] - head[2][1]) > 1.0
    assert (head[1][1] < 0 < head[2][1]) or (head[2][1] < 0 < head[1][1])
    # 杆端边不得宽于头底边（线头不穿出三角）
    assert abs(shaft[2][1] - shaft[3][1]) <= abs(head[1][1] - head[2][1]) + 1e-9

    # 极短箭头：头底边不得越过起点（杆端不反向）
    shaft, head = arrow_geometry((0, 0), (5, 0), 3)
    assert shaft[2][0] >= -1e-9
    assert shaft[2][0] < head[0][0]

    # 太短（length < 1）：无几何，由调用方画点
    assert arrow_geometry((0, 0), (0.5, 0.0), 3) == (None, None)


def test_draw_arrow_shaft_tapers_toward_head():
    """参考图样式：杆从尾端向头端线性增宽（头端处显著更宽）。

    用黑底：红色墨迹的 R 通道 > 128，背景为 0，可直接判定。
    """
    img = _blank(100, 40, color=(0, 0, 0))
    draw_arrow(img, (5, 20), (95, 20), "#ff0000", width=2)

    def red_span(x):
        ys = [y for y in range(img.height) if img.getpixel((x, y))[0] > 128]
        return max(ys) - min(ys) + 1 if ys else 0

    assert red_span(45) > red_span(12)   # 杆中段比尾端宽
    assert red_span(75) > red_span(45)   # 头端处（三角）比杆更宽


def test_draw_rect_draws_outline():
    img = _blank()
    draw_rect(img, (10, 10, 90, 90), "#00ff00", width=2)
    # 边框位置应为绿色
    assert _close(img.getpixel((10, 50)), (0, 255, 0), 25)
    assert _close(img.getpixel((50, 10)), (0, 255, 0), 25)
    # 内部应保持白色
    assert img.getpixel((50, 50)) == (255, 255, 255)


def test_draw_ellipse_draws_outline():
    img = _blank()
    draw_ellipse(img, (10, 10, 90, 90), "#0000ff", width=2)
    # 椭圆边界附近应有蓝色
    assert _close(img.getpixel((50, 11)), (0, 0, 255), 25)
    # 内部应保持白色
    assert img.getpixel((50, 50)) == (255, 255, 255)


def test_draw_brush_draws_polylines():
    img = _blank()
    draw_brush(img, [(10, 10), (50, 50), (90, 90)], "#000000", width=3)
    # 折线经过的点应为黑色
    assert _close(img.getpixel((50, 50)), (0, 0, 0), 30)
    assert _close(img.getpixel((25, 25)), (0, 0, 0), 30)
    # 折线外的区域应保持白色
    assert img.getpixel((10, 90)) == (255, 255, 255)


def test_draw_text_draws_text():
    img = _blank()
    draw_text(img, (10, 40), "Hi", "#000000", font_size=20)
    # 文字区域应有非白色像素
    has_dark = any(img.getpixel((x, y)) != (255, 255, 255) for x in range(10, 50) for y in range(35, 60))
    assert has_dark


def test_mosaic_blurs_region():
    # 用抗锯齿斜线（4x 超采样后缩回），使原图含灰度像素；
    # NEAREST 马赛克会把灰度块保留为灰度，验证"模糊"效果。
    img4x = Image.new("RGB", (400, 400), (255, 255, 255))
    d4 = ImageDraw.Draw(img4x)
    d4.line([(40, 40), (360, 360)], fill=(0, 0, 0), width=80)
    img = img4x.resize((100, 100), Image.BILINEAR)
    result = mosaic(img, 0, 0, 100, 100, block=10)
    # 马赛克后，对角线边缘的块应为非纯黑/纯白的灰度
    has_mid = any(result.getpixel((x, y)) not in ((0, 0, 0), (255, 255, 255))
                  for x in range(0, 100) for y in range(0, 100))
    assert has_mid


def test_mosaic_invalid_region_returns_copy():
    img = _blank()
    result = mosaic(img, 50, 50, 50, 50)
    assert result.size == img.size
    assert result.getpixel((10, 10)) == img.getpixel((10, 10))


class TestUndoStack:
    def test_push_pop_lifo(self):
        s = UndoStack(maxlen=10)
        s.push("a")
        s.push("b")
        assert s.pop() == "b"
        assert s.pop() == "a"
        assert s.pop() is None

    def test_maxlen_evicts_oldest(self):
        s = UndoStack(maxlen=2)
        s.push("a")
        s.push("b")
        s.push("c")
        assert s.pop() == "c"
        assert s.pop() == "b"
        assert s.pop() is None

    def test_clear(self):
        s = UndoStack(maxlen=10)
        s.push("a")
        s.clear()
        assert s.pop() is None


import time


def _full_supersample_reference(img, points, color, width, scale=4):
    """旧整图 4x 超采样的参考实现，用于逐像素对拍。"""
    from PIL import ImageDraw
    layer = Image.new("RGBA", (img.width * scale, img.height * scale), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    if len(points) == 1:
        d.ellipse([(points[0][0] - width / 2) * scale, (points[0][1] - width / 2) * scale,
                   (points[0][0] + width / 2) * scale, (points[0][1] + width / 2) * scale], fill=color)
    else:
        d.line([(x * scale, y * scale) for x, y in points], fill=color,
               width=max(1, int(width * scale)), joint="curve")
    small = layer.resize((img.width, img.height), Image.LANCZOS)
    out = img.convert("RGBA")
    out.alpha_composite(small)
    return out.convert(img.mode)


def test_draw_brush_matches_full_supersample():
    """局部超采样必须与整图超采样一致（含触及图像边缘的线段）。

    内部像素严格相等；贴边 4px 内的边界像素允许每通道 ≤8 的差异
    （Pillow 重采样在图像边界会截断并重归一化 LANCZOS 核，局部层
    几何不同导致该 1-2 级灰度差，视觉不可见）。
    """
    img = Image.new("RGB", (120, 120), (200, 210, 220))
    points = [(2, 60), (60, 2), (118, 118)]  # 触及上/左/右/下边缘
    expected = _full_supersample_reference(img, points, "#ff0000", 3)
    result = img.copy()
    draw_brush(result, points, "#ff0000", 3)
    w, h = img.size
    for i, (e, r) in enumerate(zip(expected.getdata(), result.getdata())):
        x, y = divmod(i, w)
        if 4 <= x < w - 4 and 4 <= y < h - 4:
            assert e == r, f"内部像素 ({x},{y}) 不一致: {e} != {r}"
        else:
            diff = max(abs(a - b) for a, b in zip(e, r))
            assert diff <= 8, f"边界像素 ({x},{y}) 差异过大: {e} != {r} (diff={diff})"


def test_draw_brush_single_point_matches_reference():
    img = Image.new("RGB", (60, 60), (10, 20, 30))
    expected = _full_supersample_reference(img, [(30, 30)], "#00ff00", 5)
    result = img.copy()
    draw_brush(result, [(30, 30)], "#00ff00", 5)
    assert list(result.getdata()) == list(expected.getdata())


def test_alt_pressed_state_fallback(monkeypatch):
    import drawing, sys

    class Ev:
        def __init__(self, state):
            self.state = state

    monkeypatch.setattr(sys, "platform", "linux")
    assert drawing.alt_pressed(Ev(0x20000)) is True
    assert drawing.alt_pressed(Ev(0)) is False
    assert drawing.alt_pressed(None) is False


def test_draw_brush_perf_large_image():
    """2000x1200 图上 30 段画笔应在 1 秒内完成（优化前每帧整图 4x ≈ 数十秒）。"""
    big = Image.new("RGB", (2000, 1200), (255, 255, 255))
    last = (100, 100)
    t0 = time.perf_counter()
    for i in range(30):
        p = (100 + i * 8, 100 + i * 5)
        draw_brush(big, [last, p], "#ff0000", 3)
        last = p
    elapsed = time.perf_counter() - t0
    assert elapsed < 1.0, f"30 段画笔耗时 {elapsed:.2f}s（阈值 1.0s）"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
def test_load_font_prefers_cjk_font():
    from PIL import ImageFont
    import drawing
    font = drawing._load_font(16)
    assert isinstance(font, ImageFont.FreeTypeFont)
    assert os.path.basename(font.path) in ("msyh.ttc", "msyh.ttf", "simhei.ttf", "arial.ttf")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
def test_draw_text_renders_cjk():
    """中文标注必须渲染为真实字形（足够多的非背景像素），而非豆腐字。"""
    img = Image.new("RGB", (160, 50), (255, 255, 255))
    draw_text(img, (8, 12), "中文标注", "#000000", font_size=20)
    dark = sum(1 for x in range(8, 130) for y in range(8, 45)
               if img.getpixel((x, y)) != (255, 255, 255))
    assert dark > 50


def test_load_font_fallback_when_truetype_fails(monkeypatch):
    from PIL import ImageFont
    import drawing

    real_truetype = ImageFont.truetype

    # 仅让候选文件（字符串路径）的加载失败；Pillow 的 load_default 内部用
    # BytesIO 调 truetype，必须放行，否则回退本身也会被炸掉。
    def fake_truetype(font, size, *a, **k):
        if isinstance(font, str):
            raise OSError("no font")
        return real_truetype(font, size, *a, **k)

    monkeypatch.setattr(ImageFont, "truetype", fake_truetype)
    font = drawing._load_font(17)  # 用未缓存的 size，避免命中前面测试的缓存
    assert font is not None  # 回退 load_default，不抛异常
