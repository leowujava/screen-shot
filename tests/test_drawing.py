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
