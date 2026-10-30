import tkinter as tk
from unittest.mock import MagicMock

import pytest

from selector import RegionSelector


@pytest.fixture
def fake_root():
    """让 RegionSelector 使用 mock 的 Tk 窗口，不创建真实 GUI。"""
    with pytest.MonkeyPatch.context() as m:
        mock_root = MagicMock(name="tk_root")
        mock_root.geometry = MagicMock()
        m.setattr(tk, "Tk", lambda: mock_root)
        m.setattr(tk, "Canvas", MagicMock(return_value=MagicMock(name="canvas")))
        yield mock_root


def test_virtual_coord_conversion(fake_root):
    # 窗口放在 (-1920, 0)，虚拟桌面 3840x1080
    sel = RegionSelector(x=-1920, y=0, width=3840, height=1080, on_select=lambda r: None)
    # 窗口内 (100, 50) -> 虚拟桌面 (100-1920, 50) = (-1820, 50)
    assert sel._to_virtual(100, 50) == (-1820, 50)
    # 窗口内 (2000, 100) -> 虚拟桌面 (80, 100)
    assert sel._to_virtual(2000, 100) == (80, 100)


def test_calc_region_normalized(fake_root):
    sel = RegionSelector(x=-1920, y=0, width=3840, height=1080, on_select=lambda r: None)
    # 从右往左拖拽，应归一化
    r = sel._calc_region(500, 300, 100, 100)
    # 虚拟桌面: (500-1920, 300) = (-1420, 300), (100-1920, 100) = (-1820, 100)
    # 归一化: left=-1820, top=100, width=400, height=200
    assert r == (-1820, 100, 400, 200)


def test_calc_region_min_size(fake_root):
    sel = RegionSelector(x=0, y=0, width=1920, height=1080, on_select=lambda r: None)
    # 点击太小（< 5px）视为无效，返回 None
    assert sel._calc_region(100, 100, 101, 101) is None
