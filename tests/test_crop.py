from unittest.mock import MagicMock
from PIL import Image

import editor


def test_crop_regions():
    """_do_crop 应把图像裁剪到选区，且边界安全。"""
    img = Image.new("RGB", (100, 100), "blue")
    ed = editor.Editor.__new__(editor.Editor)
    ed.image = img
    ed._off_x = 0
    ed._off_y = 0
    ed._render = MagicMock()
    ed._sync_clipboard = MagicMock()

    # 裁剪左半 100x100 -> (0,0,50,60)
    ed._do_crop((0, 0), (50, 60))
    assert ed.image.size == (50, 60)

    # 反向拖拽（从右下到左上）也能正确裁剪
    ed.image = Image.new("RGB", (80, 40), "green")
    ed._do_crop((70, 30), (10, 10))
    assert ed.image.size == (60, 20)

    # 越界坐标被限制在图像范围内
    ed.image = Image.new("RGB", (30, 20), "yellow")
    ed._do_crop((5, 5), (999, 999))
    assert ed.image.size == (25, 15)


def test_crop_too_small_ignored():
    """区域过小（宽高 0）时不改变图像。"""
    img = Image.new("RGB", (50, 50), "red")
    ed = editor.Editor.__new__(editor.Editor)
    ed.image = img
    ed._off_x = 0
    ed._off_y = 0
    ed._render = MagicMock()
    ed._sync_clipboard = MagicMock()

    ed._do_crop((10, 10), (10, 10))  # 点，宽高 0
    assert ed.image.size == (50, 50)
