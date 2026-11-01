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
    W, H = sel._actual_w, sel._actual_h
    # 正常
    assert sel._normalize((100, 100, 200, 200)) == (100, 100, 200, 200)
    # 未归一化
    assert sel._normalize((200, 200, 100, 100)) == (100, 100, 200, 200)
    # 越界
    assert sel._normalize((-50, 100, 200, 200)) == (0, 100, 200, 200)
    assert sel._normalize((100, 100, W + 80, 200)) == (100, 100, W, 200)
    # 太小
    assert sel._normalize((100, 100, 102, 102)) is None
    # 画布外
    assert sel._normalize((W + 100, H + 100, W + 200, H + 200)) is None


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
    # 区域 (70, 70, 320, 220) 经 scale 映射后转为 mss 虚拟桌面坐标
    l, t, r, b = 70, 70, 320, 220
    sx, sy = sel._scale_x, sel._scale_y
    vx_l = int(l * sx)
    vx_t = int(t * sy)
    vx_r = int(r * sx)
    vx_b = int(b * sy)
    assert sel.result == (vx_l + sel.x, vx_t + sel.y, vx_r - vx_l, vx_b - vx_t)


def test_4k_full_screen_selection(fake_root):
    """4K 屏从左上拖到右下，选区应 snap 到屏幕边缘。

    回归测试：鼠标事件坐标范围是 [0, _actual_w-1]，拖到边缘时如果
    不 snap，选区永远差 1px 选不满。
    """
    sel = RegionSelector(x=0, y=0, width=3840, height=2160, on_select=lambda r: None)
    W, H = sel._actual_w, sel._actual_h

    class E:
        def __init__(self, x, y):
            self.x, self.y = x, y

    # 按下 (10, 10)，拖到右边缘 (W-1, H-1)
    sel._on_press(E(10, 10))
    sel._on_drag(E(W - 1, H - 1))
    # 右边缘和下边缘应 snap 到 W/H
    assert sel._region[2] == W, f"右边缘没到: {sel._region[2]} != {W}"
    assert sel._region[3] == H, f"下边缘没到: {sel._region[3]} != {H}"
    assert sel._region[0] == 10
    assert sel._region[1] == 10

    # 反向：从右上 (W-1, H-1) 拖到左下 (10, 10)，也应选满
    sel2 = RegionSelector(x=0, y=0, width=3840, height=2160, on_select=lambda r: None)
    W2, H2 = sel2._actual_w, sel2._actual_h
    sel2._on_press(E(W2 - 1, H2 - 1))
    sel2._on_drag(E(10, 10))
    assert sel2._region == (10, 10, W2, H2), f"反向选满失败: {sel2._region}"

    # 中间区域不触发 snap
    sel3 = RegionSelector(x=0, y=0, width=3840, height=2160, on_select=lambda r: None)
    sel3._on_press(E(100, 100))
    sel3._on_drag(E(500, 300))
    assert sel3._region == (100, 100, 500, 300)


def test_drag_frame_cost_bounded(fake_root):
    """每帧拖动调用的 winfo_* 次数必须有界。

    回归测试：winfo_exists / winfo_width / winfo_height 每次调用都是一次
    同步 Tcl 往返 + 强制 layout 计算。在 5760x2160 虚拟桌面上，每次
    B1-Motion 都触发 _tb_show → 多次 winfo，是拖动卡顿的主要根因。
    本测试断言 100 帧拖动期间 winfo_* 总调用次数有界（≤ 10），
    确保工具条位置只在首次显示时查询，后续帧只走 geometry 移动。
    """
    sel = RegionSelector(x=0, y=0, width=3840, height=2160, on_select=lambda r: None)

    # 统计 mock 上 winfo_* 的调用次数
    winfo_count = {"exists": 0, "width": 0, "height": 0, "rootx": 0}
    tb_win = sel.tb_win

    orig_exists = tb_win.winfo_exists
    orig_width = tb_win.winfo_width
    orig_height = tb_win.winfo_height

    def count_exists(*a, **kw):
        winfo_count["exists"] += 1
        return orig_exists(*a, **kw)

    def count_width(*a, **kw):
        winfo_count["width"] += 1
        return orig_width(*a, **kw)

    def count_height(*a, **kw):
        winfo_count["height"] += 1
        return orig_height(*a, **kw)

    tb_win.winfo_exists = count_exists
    tb_win.winfo_width = count_width
    tb_win.winfo_height = count_height

    class E:
        def __init__(self):
            self.x = 0
            self.y = 0

    # 模拟 100 帧拖动
    for i in range(100):
        x = 100 + i
        y = 200 + (i // 2)
        ev = E()
        ev.x, ev.y = x, y
        if i == 0:
            sel._on_press(ev)
        else:
            sel._on_drag(ev)

    # 100 帧拖动，winfo_exists 应该只调 ~1 次（首次显示时）
    # winfo_width / winfo_height 应该只调 ~1 次（首次显示时，之后缓存）
    # 总计 winfo 调用次数应 ≤ 10
    total_winfo = sum(winfo_count.values())
    assert total_winfo <= 10, (
        f"100 帧拖动 winfo 总调用 {total_winfo} 次（{winfo_count}），"
        f"超出预期上限 10，工具条布局计算未优化"
    )
