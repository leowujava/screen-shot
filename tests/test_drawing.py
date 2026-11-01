import pytest
from PIL import Image, ImageDraw
from drawing import (
    draw_arrow, draw_rect, draw_ellipse, draw_brush, draw_text, mosaic, UndoStack,
)


def _blank(w=100, h=100, color=(255, 255, 255)):
    return Image.new("RGB", (w, h), color)


def test_draw_arrow_draws_line_and_head():
    img = _blank()
    draw_arrow(img, (10, 10), (90, 90), "#ff0000", width=3)
    # 中心点应为红色（箭头线条经过）
    assert img.getpixel((50, 50)) == (255, 0, 0)
    # 箭头尖端附近也应有红色
    assert img.getpixel((88, 88)) == (255, 0, 0)
    # 起点和终点之外的区域应保持白色
    assert img.getpixel((5, 5)) == (255, 255, 255)
    assert img.getpixel((95, 5)) == (255, 255, 255)


def test_draw_rect_draws_outline():
    img = _blank()
    draw_rect(img, (10, 10, 90, 90), "#00ff00", width=2)
    # 边框位置应为绿色
    assert img.getpixel((10, 50)) == (0, 255, 0)
    assert img.getpixel((50, 10)) == (0, 255, 0)
    # 内部应保持白色
    assert img.getpixel((50, 50)) == (255, 255, 255)


def test_draw_ellipse_draws_outline():
    img = _blank()
    draw_ellipse(img, (10, 10, 90, 90), "#0000ff", width=2)
    # 椭圆边界附近应有蓝色
    assert img.getpixel((50, 11)) == (0, 0, 255)
    # 内部应保持白色
    assert img.getpixel((50, 50)) == (255, 255, 255)


def test_draw_brush_draws_polylines():
    img = _blank()
    draw_brush(img, [(10, 10), (50, 50), (90, 90)], "#000000", width=3)
    # 折线经过的点应为黑色
    assert img.getpixel((50, 50)) == (0, 0, 0)
    assert img.getpixel((25, 25)) == (0, 0, 0)
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
