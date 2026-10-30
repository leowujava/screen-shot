from PIL import Image

from editor import UndoStack, mosaic


def test_undo_stack_basic():
    s = UndoStack(maxlen=10)
    s.push(1)
    s.push(2)
    s.push(3)
    assert s.pop() == 3
    assert s.pop() == 2
    assert s.pop() == 1
    assert s.pop() is None


def test_undo_stack_maxlen():
    s = UndoStack(maxlen=3)
    for i in range(5):
        s.push(i)
    assert s.pop() == 4
    assert s.pop() == 3
    assert s.pop() == 2
    assert s.pop() is None


def test_mosaic_reduces_detail():
    img = Image.new("RGB", (100, 100), (255, 0, 0))
    # 画一条细线，马赛克后细节应被模糊
    for i in range(50):
        img.putpixel((i, 50), (0, 0, 255))
    result = mosaic(img, 0, 40, 100, 60, block=8)
    assert result.size == img.size
    # 原细线位置 (48, 50) 马赛克后不应保持纯蓝
    px = result.getpixel((48, 50))
    assert px != (0, 0, 255)
    # 区域外像素不受影响
    assert result.getpixel((48, 20)) == (255, 0, 0)
