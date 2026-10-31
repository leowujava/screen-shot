import tkinter as tk
from unittest.mock import MagicMock

import pytest

from selector import RegionSelector


@pytest.fixture
def fake_root():
    """让 RegionSelector 使用 mock 的 Tk 窗口，不创建真实 GUI。

    同时打桩 PIL.ImageTk / capture，避免依赖真实截屏或 PIL 渲染。
    """
    import sys
    import types

    with pytest.MonkeyPatch.context() as m:
        mock_root = MagicMock(name="tk_root")
        mock_root.geometry = MagicMock()
        m.setattr(tk, "Tk", lambda: mock_root)
        m.setattr(tk, "Canvas", MagicMock(return_value=MagicMock(name="canvas")))
        tb_win = MagicMock(name="tb_win")
        tb_win.winfo_width.return_value = 100
        tb_win.winfo_height.return_value = 30
        tb_win.winfo_exists.return_value = True
        m.setattr(tk, "Toplevel", MagicMock(return_value=tb_win))
        m.setattr(tk, "Frame", MagicMock(return_value=MagicMock(name="frame")))
        m.setattr(tk, "Label", MagicMock(return_value=MagicMock(name="label")))

        # 假 PIL 模块
        fake_pil = types.ModuleType("PIL")
        fake_pil_image = types.ModuleType("PIL.Image")
        fake_pil_imagetk = types.ModuleType("PIL.ImageTk")
        fake_pil_imagetk.PhotoImage = MagicMock(name="PhotoImage")
        fake_pil.Image = fake_pil_image
        fake_pil.ImageTk = fake_pil_imagetk
        m.setitem(sys.modules, "PIL", fake_pil)
        m.setitem(sys.modules, "PIL.Image", fake_pil_image)
        m.setitem(sys.modules, "PIL.ImageTk", fake_pil_imagetk)

        # 假 capture 模块（避免 mss）
        fake_capture = types.ModuleType("capture")
        fake_capture.capture_fullscreen = MagicMock(return_value=None)
        m.setitem(sys.modules, "capture", fake_capture)

        # 让 threading.Thread 不真正启动（避免后台抓图线程）
        import threading
        real_thread = threading.Thread

        class FakeThread:
            def __init__(self, target=None, daemon=None, **kw):
                pass

            def start(self):
                pass

        m.setattr(threading, "Thread", FakeThread)

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


def test_normalize(fake_root):
    sel = RegionSelector(x=0, y=0, width=1920, height=1080, on_select=lambda r: None)
    # 正常
    assert sel._normalize((100, 100, 200, 200)) == (100, 100, 200, 200)
    # 未归一化
    assert sel._normalize((200, 200, 100, 100)) == (100, 100, 200, 200)
    # 越界
    assert sel._normalize((-50, 100, 200, 200)) == (0, 100, 200, 200)
    assert sel._normalize((100, 100, 2000, 200)) == (100, 100, 1920, 200)
    # 太小
    assert sel._normalize((100, 100, 102, 102)) is None
    # 画布外
    assert sel._normalize((2000, 2000, 2100, 2100)) is None


def test_handle_coords(fake_root):
    sel = RegionSelector(x=0, y=0, width=1920, height=1080, on_select=lambda r: None)
    hc = sel._handle_coords(100, 100, 300, 200)
    assert hc["nw"] == (100, 100)
    assert hc["n"] == (200, 100)
    assert hc["ne"] == (300, 100)
    assert hc["e"] == (300, 150)
    assert hc["se"] == (300, 200)
    assert hc["s"] == (200, 200)
    assert hc["sw"] == (100, 200)
    assert hc["w"] == (100, 150)


def test_hit_test(fake_root):
    sel = RegionSelector(x=0, y=0, width=1920, height=1080, on_select=lambda r: None)
    sel._mode = "adjust"
    sel._region = (100, 100, 300, 200)
    assert sel._hit_test(100, 100) == "nw"
    assert sel._hit_test(200, 100) == "n"
    assert sel._hit_test(300, 200) == "se"
    assert sel._hit_test(200, 150) is None
    assert sel._hit_test(400, 300) is None


def test_select_to_adjust_flow(fake_root):
    """模拟 select → adjust 流程。"""
    sel = RegionSelector(x=0, y=0, width=1920, height=1080, on_select=lambda r: None)
    assert sel._mode == "select"

    class E:
        def __init__(self, x, y):
            self.x, self.y = x, y

    sel._on_press(E(100, 100))
    assert sel.start == (100, 100)

    sel._on_drag(E(300, 200))
    assert sel._mode == "adjust"
    assert sel._region == (100, 100, 300, 200)

    sel._on_release(E(300, 200))
    assert sel._mode == "adjust"

    # 拖动 nw 手柄
    sel._on_press(E(100, 100))
    assert sel._drag_op == ("nw", "resize")
    sel._on_drag(E(50, 50))
    assert sel._region == (50, 50, 300, 200)
    sel._on_release(E(50, 50))

    # 拖动内部 → 移动
    sel._on_press(E(200, 150))
    assert sel._drag_op == ("move", "move")
    sel._on_drag(E(220, 170))
    assert sel._region == (70, 70, 320, 220)
    sel._on_release(E(220, 170))

    # 确认
    sel._confirm()
    assert sel.result is not None
    # 区域 (70, 70, 320, 220) 转为 (left, top, w, h) 并加上窗口偏移 (0, 0)
    assert sel.result == (70, 70, 250, 150)


def test_4k_full_screen_selection(fake_root):
    """4K 屏（3840x2160）从左上拖到右下，选区应 snap 到屏幕边缘。

    回归测试：鼠标事件坐标范围是 [0, width-1]，拖到边缘时如果
    不 snap，选区永远差 1px 选不满。
    """
    sel = RegionSelector(x=0, y=0, width=3840, height=2160, on_select=lambda r: None)

    class E:
        def __init__(self, x, y):
            self.x, self.y = x, y

    # 按下 (10, 10)，拖到右边缘 (3839, 2159)
    sel._on_press(E(10, 10))
    sel._on_drag(E(3839, 2159))
    # 右边缘和下边缘应 snap 到 width/height
    assert sel._region[2] == 3840, f"右边缘没到: {sel._region[2]}"
    assert sel._region[3] == 2160, f"下边缘没到: {sel._region[3]}"
    assert sel._region[0] == 10
    assert sel._region[1] == 10

    # 反向：从右上 (3839, 2159) 拖到左下 (10, 10)，也应选满
    sel2 = RegionSelector(x=0, y=0, width=3840, height=2160, on_select=lambda r: None)
    sel2._on_press(E(3839, 2159))
    sel2._on_drag(E(10, 10))
    assert sel2._region == (10, 10, 3840, 2160), f"反向选满失败: {sel2._region}"

    # 中间区域不触发 snap
    sel3 = RegionSelector(x=0, y=0, width=3840, height=2160, on_select=lambda r: None)
    sel3._on_press(E(100, 100))
    sel3._on_drag(E(500, 300))
    assert sel3._region == (100, 100, 500, 300)
