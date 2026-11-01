import tkinter as tk
from unittest.mock import MagicMock
from PIL import Image, ImageDraw

import pytest

from selector import Selector


@pytest.fixture
def fake_root():
    """让 Selector 使用 mock 的 Tk 窗口，不创建真实 GUI。

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
        m.setattr(tk, "Spinbox", MagicMock(return_value=MagicMock(name="spinbox")))
        m.setattr(tk, "StringVar", MagicMock(return_value=MagicMock(name="stringvar")))

        # 假 PIL 模块
        fake_pil = types.ModuleType("PIL")
        fake_pil_image = types.ModuleType("PIL.Image")
        fake_pil_image.BILINEAR = 2
        fake_pil_image.NEAREST = 0

        class FakeImage:
            def __init__(self, mode, size, color=None):
                self.size = size
                self.mode = mode

            def crop(self, box):
                l, t, r, b = box
                return FakeImage(self.mode, (r - l, b - t))

            def copy(self):
                return FakeImage(self.mode, self.size)

            def getpixel(self, pos):
                return (255, 255, 255)

        fake_pil_image.new = lambda mode, size, color=None: FakeImage(mode, size, color)
        fake_pil_image.open = MagicMock()
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

        # 屏蔽 Win32 GetSystemMetrics，避免真实屏幕尺寸干扰测试
        import ctypes
        m.setattr(ctypes.windll.user32, "GetSystemMetrics",
                  lambda _n: 0, raising=False)

        # 屏蔽 ImageTk.PhotoImage（需要真实 Tk 解释器）
        import selector as sel_mod
        m.setattr(sel_mod.ImageTk, "PhotoImage",
                  MagicMock(name="PhotoImage"), raising=True)

        yield mock_root


# =================================================================== #
# Task 1: 回调签名测试
# =================================================================== #

def test_selector_confirm_passes_region_and_img(fake_root):
    """确认时 on_select 收到 (region, annotated_img)。"""
    full = Image.new("RGB", (200, 200), (255, 255, 255))
    results = {}
    sel = Selector(
        x=0, y=0, width=200, height=200, full_img=full,
        on_select=lambda region, img: results.update(region=region, img=img),
        on_copy=lambda region, img: None,
        on_save=lambda region, img: None,
    )
    # 模拟：进入 adjust，region = (10, 10, 100, 80)
    sel._mode = "adjust"
    sel._region = (10, 10, 100, 80)
    # 从 full_img crop 出 region_img
    sel._init_region_img()
    sel._confirm()
    # after(0, _exit) 在 mock 上不会自动执行，手动触发
    sel._exit()
    assert "region" in results and "img" in results
    # region 是 mss 虚拟桌面坐标（x=0,y=0 时等于 canvas 坐标）
    assert results["region"] == (10, 10, 90, 70)
    # img 是 90x70 的 PIL Image
    assert results["img"].size == (90, 70)


def test_selector_cancel_passes_none(fake_root):
    """取消时 on_select 收到 (None, None)。"""
    results = {}
    sel = Selector(
        x=0, y=0, width=200, height=200,
        full_img=None,
        on_select=lambda region, img: results.update(region=region, img=img),
        on_copy=lambda region, img: None,
        on_save=lambda region, img: None,
    )
    sel._cancel()
    sel._exit()
    assert results["region"] is None
    assert results["img"] is None


# =================================================================== #
# 现有测试（适配新类名 + full_img 参数）
# =================================================================== #

def test_virtual_coord_conversion(fake_root):
    # 窗口放在 (-1920, 0)，虚拟桌面 3840x1080
    sel = Selector(x=-1920, y=0, width=3840, height=1080, full_img=None,
                   on_select=lambda r, i: None,
                   on_copy=lambda r, i: None,
                   on_save=lambda r, i: None)
    # 窗口内 (100, 50) -> 虚拟桌面 (100-1920, 50) = (-1820, 50)
    assert sel._to_virtual(100, 50) == (-1820, 50)
    # 窗口内 (2000, 100) -> 虚拟桌面 (80, 100)
    assert sel._to_virtual(2000, 100) == (80, 100)


def test_calc_region_normalized(fake_root):
    sel = Selector(x=-1920, y=0, width=3840, height=1080, full_img=None,
                   on_select=lambda r, i: None,
                   on_copy=lambda r, i: None,
                   on_save=lambda r, i: None)
    # 从右往左拖拽，应归一化
    r = sel._calc_region(500, 300, 100, 100)
    # 虚拟桌面: (500-1920, 300) = (-1420, 300), (100-1920, 100) = (-1820, 100)
    # 归一化: left=-1820, top=100, width=400, height=200
    assert r == (-1820, 100, 400, 200)


def test_calc_region_min_size(fake_root):
    sel = Selector(x=0, y=0, width=1920, height=1080, full_img=None,
                   on_select=lambda r, i: None,
                   on_copy=lambda r, i: None,
                   on_save=lambda r, i: None)
    # 点击太小（< 5px）视为无效，返回 None
    assert sel._calc_region(100, 100, 101, 101) is None


def test_normalize(fake_root):
    sel = Selector(x=0, y=0, width=1920, height=1080, full_img=None,
                   on_select=lambda r, i: None,
                   on_copy=lambda r, i: None,
                   on_save=lambda r, i: None)
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
    sel = Selector(x=0, y=0, width=1920, height=1080, full_img=None,
                   on_select=lambda r, i: None,
                   on_copy=lambda r, i: None,
                   on_save=lambda r, i: None)
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
    sel = Selector(x=0, y=0, width=1920, height=1080, full_img=None,
                   on_select=lambda r, i: None,
                   on_copy=lambda r, i: None,
                   on_save=lambda r, i: None)
    sel._mode = "adjust"
    sel._region = (100, 100, 300, 200)
    assert sel._hit_test(100, 100) == "nw"
    assert sel._hit_test(200, 100) == "n"
    assert sel._hit_test(300, 200) == "se"
    assert sel._hit_test(200, 150) is None
    assert sel._hit_test(400, 300) is None


def test_select_to_adjust_flow(fake_root):
    """模拟 select → adjust 流程。"""
    sel = Selector(x=0, y=0, width=1920, height=1080, full_img=None,
                   on_select=lambda r, i: None,
                   on_copy=lambda r, i: None,
                   on_save=lambda r, i: None)
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

    # 确认
    sel._confirm()
    assert sel.result is not None
    # 区域 (50, 50, 300, 200) 经 scale 映射后转为 mss 虚拟桌面坐标
    l, t, r, b = 50, 50, 300, 200
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
    sel = Selector(x=0, y=0, width=3840, height=2160, full_img=None,
                   on_select=lambda r, i: None,
                   on_copy=lambda r, i: None,
                   on_save=lambda r, i: None)
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
    sel2 = Selector(x=0, y=0, width=3840, height=2160, full_img=None,
                    on_select=lambda r, i: None,
                    on_copy=lambda r, i: None,
                    on_save=lambda r, i: None)
    W2, H2 = sel2._actual_w, sel2._actual_h
    sel2._on_press(E(W2 - 1, H2 - 1))
    sel2._on_drag(E(10, 10))
    assert sel2._region == (10, 10, W2, H2), f"反向选满失败: {sel2._region}"

    # 中间区域不触发 snap
    sel3 = Selector(x=0, y=0, width=3840, height=2160, full_img=None,
                    on_select=lambda r, i: None,
                    on_copy=lambda r, i: None,
                    on_save=lambda r, i: None)
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
    sel = Selector(x=0, y=0, width=3840, height=2160, full_img=None,
                   on_select=lambda r, i: None,
                   on_copy=lambda r, i: None,
                   on_save=lambda r, i: None)

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


# =================================================================== #
# Task 2: 工具条按钮测试
# =================================================================== #

def test_toolbar_has_all_buttons(fake_root):
    """工具条必须包含完整按钮集合。"""
    sel = Selector(x=0, y=0, width=1920, height=1080, full_img=None,
                   on_select=lambda r, i: None,
                   on_copy=lambda r, i: None,
                   on_save=lambda r, i: None)
    # 工具按钮
    for tool in ("brush", "rect", "ellipse", "arrow", "mosaic", "text"):
        assert tool in sel._tool_btns, f"缺少工具按钮: {tool}"
    # 操作按钮
    for key in ("confirm", "copy", "save", "cancel"):
        assert key in sel._tb_btns, f"缺少操作按钮: {key}"
    # 每个按钮都绑定了 <Button-1>
    for b in list(sel._tool_btns.values()) + list(sel._tb_btns.values()):
        bound = [c.args[0] for c in b.bind.call_args_list
                 if c.args and c.args[0] == "<Button-1>"]
        assert bound, f"按钮未绑定点击事件: {b}"


def test_select_tool_updates_state(fake_root):
    """点击工具按钮应更新 self.tool。"""
    sel = Selector(x=0, y=0, width=1920, height=1080, full_img=None,
                   on_select=lambda r, i: None,
                   on_copy=lambda r, i: None,
                   on_save=lambda r, i: None)
    sel._select_tool("arrow")
    assert sel.tool == "arrow"
    # 选中按钮高亮，其他恢复
    assert sel._tool_btns["arrow"].configure.call_args is not None


# =================================================================== #
# Task 3: 标注绘制测试
# =================================================================== #

def test_brush_annotation_draws_on_region_img(fake_root):
    full = Image.new("RGB", (100, 100), (255, 255, 255))
    sel = Selector(x=0, y=0, width=100, height=100, full_img=full,
                   on_select=lambda r, i: None,
                   on_copy=lambda r, i: None,
                   on_save=lambda r, i: None)
    sel._mode = "adjust"
    sel._region = (10, 10, 90, 90)
    sel._init_region_img()
    sel.tool = "brush"; sel.color = "#000000"; sel.width_attr = 3

    class E:
        def __init__(self, x, y): self.x, self.y = x, y
    sel._on_press(E(20, 20))
    sel._on_drag(E(60, 60))
    sel._on_release(E(60, 60))
    # 画笔线条经过 (30, 30)
    assert sel._region_img.getpixel((20, 20)) == (0, 0, 0)


def test_rect_annotation_draws_on_region_img(fake_root):
    full = Image.new("RGB", (100, 100), (255, 255, 255))
    sel = Selector(x=0, y=0, width=100, height=100, full_img=full,
                   on_select=lambda r, i: None,
                   on_copy=lambda r, i: None,
                   on_save=lambda r, i: None)
    sel._mode = "adjust"
    sel._region = (10, 10, 90, 90)
    sel._init_region_img()
    sel.tool = "rect"; sel.color = "#ff0000"; sel.width_attr = 2

    class E:
        def __init__(self, x, y): self.x, self.y = x, y
    sel._on_press(E(20, 20))
    sel._on_release(E(80, 80))
    assert sel._region_img.getpixel((10, 40)) == (255, 0, 0)


def test_undo_reverts_annotation(fake_root):
    full = Image.new("RGB", (100, 100), (255, 255, 255))
    sel = Selector(x=0, y=0, width=100, height=100, full_img=full,
                   on_select=lambda r, i: None,
                   on_copy=lambda r, i: None,
                   on_save=lambda r, i: None)
    sel._mode = "adjust"
    sel._region = (10, 10, 90, 90)
    sel._init_region_img()
    sel.tool = "rect"; sel.color = "#ff0000"; sel.width_attr = 2

    class E:
        def __init__(self, x, y): self.x, self.y = x, y
    sel._on_press(E(20, 20))
    sel._on_release(E(80, 80))
    sel._undo()
    assert sel._region_img.getpixel((40, 40)) == (255, 255, 255)


def test_mosaic_annotation(fake_root):
    full = Image.new("RGB", (100, 100), (255, 255, 255))
    d = ImageDraw.Draw(full)
    d.line([(10, 10), (90, 90)], fill=(0, 0, 0), width=5)
    sel = Selector(x=0, y=0, width=100, height=100, full_img=full,
                   on_select=lambda r, i: None,
                   on_copy=lambda r, i: None,
                   on_save=lambda r, i: None)
    sel._mode = "adjust"
    sel._region = (0, 0, 100, 100)
    sel._init_region_img()
    sel.tool = "mosaic"; sel.width_attr = 8

    class E:
        def __init__(self, x, y): self.x, self.y = x, y
    sel._on_press(E(10, 10))
    sel._on_release(E(90, 90))
    has_mid = any(sel._region_img.getpixel((x, y)) not in ((0, 0, 0), (255, 255, 255))
                  for x in range(0, 100, 5) for y in range(0, 100, 5))
    assert has_mid


def test_text_annotation(fake_root):
    from unittest.mock import patch
    full = Image.new("RGB", (100, 100), (255, 255, 255))
    sel = Selector(x=0, y=0, width=100, height=100, full_img=full,
                   on_select=lambda r, i: None,
                   on_copy=lambda r, i: None,
                   on_save=lambda r, i: None)
    sel._mode = "adjust"
    sel._region = (10, 10, 90, 90)
    sel._init_region_img()
    sel.tool = "text"; sel.color = "#000000"

    with patch.object(sel, "_input_text_dialog") as mock_dialog:
        mock_dialog.return_value = "Hi"
        sel._input_text(20, 20)
    has_dark = any(sel._region_img.getpixel((x, y)) != (255, 255, 255)
                   for x in range(15, 45) for y in range(15, 35))
    assert has_dark


# =================================================================== #
# Task 4: 渲染测试
# =================================================================== #

def test_render_all_in_adjust_mode_creates_masks_and_region(fake_root):
    """adjust 模式下 _render_all 应创建 4 块遮罩 + 选区图。"""
    full = Image.new("RGB", (200, 200), (255, 255, 255))
    sel = Selector(x=0, y=0, width=200, height=200, full_img=full,
                   on_select=lambda r, i: None,
                   on_copy=lambda r, i: None,
                   on_save=lambda r, i: None)
    sel._mode = "adjust"
    sel._region = (50, 50, 150, 150)
    sel._init_region_img()
    sel._render_all()
    # _items 里应有 bg / mask1-4 / region / rect / handles
    assert "bg" in sel._items, "缺少背景"
    for i in range(1, 5):
        assert f"mask{i}" in sel._items, f"缺少遮罩 mask{i}"
    assert "region" in sel._items, "缺少选区图"
    assert "rect" in sel._items, "缺少选区边框"
    assert "handles" in sel._items, "缺少手柄"


def test_render_all_in_select_mode_no_masks(fake_root):
    """select 模式下 _render_all 不应创建遮罩/选区图。"""
    sel = Selector(x=0, y=0, width=200, height=200, full_img=None,
                   on_select=lambda r, i: None,
                   on_copy=lambda r, i: None,
                   on_save=lambda r, i: None)
    sel._mode = "select"
    sel._render_all()
    # select 模式不画遮罩
    for i in range(1, 5):
        assert f"mask{i}" not in sel._items, f"select 模式不应有 mask{i}"
