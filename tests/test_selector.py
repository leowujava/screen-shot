"""Selector（PySide6 版）行为测试。

GUI 全部走 Qt offscreen 平台（见 conftest.py），构造真实的 Selector 窗口，
不 mock Tk/画布；渲染类断言用 QWidget.render 取像素。
"""
import time
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image, ImageDraw

from PySide6.QtCore import QEvent, QRect, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QToolButton

import selector as sel_mod
from selector import BORDER_INNER, Selector


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #

class Event:
    """模拟鼠标事件：.x/.y + button()（兼容 _event_pos 与 Qt 按钮判定）。"""

    def __init__(self, x, y, button=Qt.LeftButton):
        self.x, self.y = x, y
        self._button = button

    def button(self):
        return self._button


@pytest.fixture
def sel_env(qt_app, monkeypatch):
    """让 _virtual_screen_size 查询失败 → Selector 回退到传入的 width/height。

    保持旧版测试语义：_actual_w/h == 传入尺寸（scale 1.0、坐标 1:1）。
    """
    monkeypatch.setattr(sel_mod, "_virtual_screen_size", lambda: (0, 0))
    return sel_mod


def _make(sel_env, x=0, y=0, width=200, height=200, full_img=None,
          capture_screen=None, **kw):
    return Selector(
        x=x, y=y, width=width, height=height, full_img=full_img,
        on_select=kw.pop("on_select", lambda region, img: None),
        on_copy=kw.pop("on_copy", lambda region, img: None),
        on_save=kw.pop("on_save", lambda region, img: None),
        capture_screen=capture_screen,
        **kw,
    )


def _render_image(sel):
    """把 overlay 当前状态渲染成 QImage（offscreen 下可用）。"""
    pm = QPixmap(sel.width(), sel.height())
    sel.render(pm)
    return pm.toImage()


def _rgb(img, x, y):
    c = img.pixelColor(x, y)
    return (c.red(), c.green(), c.blue())


def _close(a, b, tol=40):
    """颜色分量接近判定（抗锯齿后边缘存在灰度过渡，允许容差）。"""
    return all(abs(x - y) <= tol for x, y in zip(a, b))


# =================================================================== #
# 回调签名
# =================================================================== #

def test_selector_confirm_passes_region_and_img(sel_env):
    """确认时 on_select 收到 (region, annotated_img)。"""
    full = Image.new("RGB", (200, 200), (255, 255, 255))
    results = {}
    sel = _make(sel_env, full_img=full,
                on_select=lambda region, img: results.update(region=region, img=img))
    # 模拟：进入 adjust，region = (10, 10, 100, 80)
    sel._mode = "adjust"
    sel._region = (10, 10, 100, 80)
    # 从 full_img crop 出 region_img
    sel._init_region_img()
    sel._confirm()
    assert "region" in results and "img" in results
    # region 是 mss 虚拟桌面坐标（x=0,y=0 时等于 canvas 坐标）
    assert results["region"] == (10, 10, 90, 70)
    # img 是 90x70 的 PIL Image
    assert results["img"].size == (90, 70)


def test_selector_cancel_passes_none(sel_env):
    """取消时 on_select 收到 (None, None)。"""
    results = {}
    sel = _make(sel_env, on_select=lambda region, img: results.update(region=region, img=img))
    sel._cancel()
    assert results["region"] is None
    assert results["img"] is None


def test_selector_releases_heavy_memory_after_finish(sel_env):
    """选区结束（回调完成）后释放大块内存：定格截图/底图 pixmap/选区图/撤销快照。

    回归：窗口 hide 后仍被 _selector_holder 引用，多屏定格截图（数十 MB）
    会一直滞留到下一次框选。释放必须在回调之后（回调 lambda 调用时读取
    self._region_img）。
    """
    full = Image.new("RGB", (100, 100), (255, 255, 255))
    seen = {}
    sel = _make(sel_env, width=100, height=100, full_img=full,
                on_select=lambda region, img: seen.update(region=region, img=img))
    sel._mode = "adjust"
    sel._region = (10, 10, 90, 90)
    sel._init_region_img()
    sel._render_all()
    sel.undo.push(sel._region_img.copy())
    assert sel.full_img is not None
    assert sel._base_pixmap is not None
    assert sel._region_pixmap is not None

    sel._confirm()

    # 回调仍能拿到选区图（释放排在回调之后）
    assert seen["img"] is not None and seen["img"].size == (80, 80)
    # 结束后大块内存已释放
    assert sel.full_img is None
    assert sel._base_pixmap is None
    assert sel._region_img is None
    assert sel._region_pixmap is None
    assert len(sel.undo) == 0


def test_toolbar_keypress_routes_to_selector(sel_env):
    """工具条按键转发给 selector（回归：_Toolbar(None) 导致 _selector 为 None 崩溃）。"""
    from PySide6.QtGui import QKeyEvent
    sel = _make(sel_env)
    assert sel.tb_win._selector is sel
    calls = []
    sel._cancel = lambda: calls.append("cancel")
    sel._confirm = lambda: calls.append("confirm")
    sel._undo = lambda: calls.append("undo")
    sel.tb_win.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    sel.tb_win.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key_Enter, Qt.NoModifier))
    sel.tb_win.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key_Z, Qt.ControlModifier))
    assert calls == ["cancel", "confirm", "undo"]


def test_toolbar_keypress_ignores_when_selector_gone(sel_env):
    """_selector 为 None（selector 已清理）时按键直接忽略，不抛 AttributeError。"""
    from PySide6.QtGui import QKeyEvent
    tb = sel_mod._Toolbar(None)
    tb.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key_Escape, Qt.NoModifier))


def test_selector_captures_screen_after_initial_selection(sel_env):
    """首次框选完成时才捕获整屏，选框移动时显示截图对应位置。"""
    full = Image.new("RGB", (200, 200), (255, 255, 255))
    capture_screen = MagicMock(return_value=full)
    sel = _make(sel_env, full_img=None, capture_screen=capture_screen)

    assert sel.full_img is None
    assert sel._base_pixmap is None
    # Qt 把窗口透明度量化到 8 位（0.5 → 127/255）
    assert sel.windowOpacity() == pytest.approx(0.5, abs=0.01)
    capture_screen.assert_not_called()

    with patch.object(sel, "_force_focus", wraps=sel._force_focus) as ff:
        sel.mousePressEvent(Event(20, 20))
        sel.mouseMoveEvent(Event(80, 80))
        capture_screen.assert_not_called()
        sel.mouseReleaseEvent(Event(80, 80))

    capture_screen.assert_called_once_with()
    assert sel.full_img is full
    assert sel._base_pixmap is not None
    # 选区 crop 完成（_init_region_img）后，选区图应已渲染（pixmap 已缓存）
    assert sel._region_img is not None
    assert sel._region_pixmap is not None
    assert sel.windowOpacity() == pytest.approx(1.0, abs=0.01)
    # 画框完成（_freeze_selection 重映射 overlay）后，焦点应被强制拉回 overlay，
    # 避免键盘输入落到之前的应用里（松开鼠标后直接可用 Ctrl+Z / Esc 等）
    ff.assert_called()


def test_selection_defaults_to_move_tool_and_can_be_dragged(sel_env):
    full = Image.new("RGB", (200, 200), (255, 255, 255))
    sel = _make(sel_env, full_img=full)
    assert sel.tool == "move"
    assert sel._cursor == Qt.CrossCursor

    # Create a selection, then drag from its interior to move the whole frame.
    sel.mousePressEvent(Event(20, 20))
    with patch.object(sel, "_init_region_img", wraps=sel._init_region_img) as crop:
        sel.mouseMoveEvent(Event(80, 80))
        crop.assert_not_called()
        sel.mouseReleaseEvent(Event(80, 80))
        crop.assert_called_once()
    sel.mousePressEvent(Event(40, 40))
    assert sel._drag_op == ("move", "move")
    assert sel._cursor == Qt.SizeAllCursor
    sel.mouseMoveEvent(Event(50, 55))
    assert sel._region == (30, 35, 90, 95)


def test_moving_selection_reveals_new_area_of_fixed_screenshot(sel_env):
    full = Image.new("RGB", (200, 200), (255, 255, 255))
    sel = _make(sel_env, full_img=full)

    sel.mousePressEvent(Event(20, 20))
    sel.mouseMoveEvent(Event(80, 80))
    sel.mouseReleaseEvent(Event(80, 80))
    old_crop = sel._region_img

    sel.mousePressEvent(Event(40, 40))
    assert sel._masks_hidden is True
    with patch.object(sel, "_init_region_img") as crop:
        sel.mouseMoveEvent(Event(50, 55))

    assert sel._region == (30, 35, 90, 95)
    # 移动期间取景框直接透出定格底图（选区图撤下），不重裁
    assert sel._region_pixmap is None
    assert sel._region_img is old_crop
    crop.assert_not_called()

    sel.mouseReleaseEvent(Event(50, 55))
    assert sel._region_img is not old_crop
    assert sel._masks_hidden is False


def test_moving_selection_keeps_annotations(sel_env):
    """移动选区框后标注不丢失：标注跟随内容移动，撤销栈保留（回归：移动清空标注）。"""
    # 全白底 + 右侧一条黑带：移动选区跨入黑带可验证底图确实换了位置
    full = Image.new("RGB", (200, 200), (255, 255, 255))
    ImageDraw.Draw(full).rectangle([120, 0, 139, 199], fill=(0, 0, 0))
    sel = _make(sel_env, full_img=full)
    # 画选区 (20,20,80,80)，60x60
    sel.mousePressEvent(Event(20, 20))
    sel.mouseMoveEvent(Event(80, 80))
    sel.mouseReleaseEvent(Event(80, 80))
    # 画一个红色矩形标注（局部 10,10 - 40,40）
    sel._select_tool("rect")
    sel.mousePressEvent(Event(30, 30))
    sel.mouseMoveEvent(Event(60, 60))
    sel.mouseReleaseEvent(Event(60, 60))
    assert len(sel.undo) == 1
    assert _close(sel._region_img.getpixel((10, 25)), (245, 72, 77), 40)
    # 整体移动选区：(20,20,80,80) → (120,20,180,80)（跨入黑带）
    sel._select_tool("move")
    sel.mousePressEvent(Event(40, 40))
    sel.mouseMoveEvent(Event(140, 40))
    sel.mouseReleaseEvent(Event(140, 40))
    assert sel._region == (120, 20, 180, 80)
    # 底图已移到新位置（黑带在选区左侧局部 x 0..19）
    assert sel._region_img.getpixel((5, 25)) == (0, 0, 0)
    # 标注仍在原局部位置（跟随内容移动）
    assert _close(sel._region_img.getpixel((10, 25)), (245, 72, 77), 40)
    # 撤销栈保留：Ctrl+Z 可撤销该矩形
    assert len(sel.undo) == 1
    sel._undo()
    assert sel._region_img.getpixel((10, 25)) == (0, 0, 0)
    assert len(sel.undo) == 0


# =================================================================== #
# 坐标 / 区域计算
# =================================================================== #

def test_virtual_coord_conversion(sel_env):
    # 窗口放在 (-1920, 0)，虚拟桌面 3840x1080
    sel = _make(sel_env, x=-1920, width=3840, height=1080)
    # 窗口内 (100, 50) -> 虚拟桌面 (100-1920, 50) = (-1820, 50)
    assert sel._to_virtual(100, 50) == (-1820, 50)
    # 窗口内 (2000, 100) -> 虚拟桌面 (80, 100)
    assert sel._to_virtual(2000, 100) == (80, 100)


def test_to_virtual_region(sel_env):
    sel = _make(sel_env, x=-1920, width=3840, height=1080)
    assert sel._to_virtual_region() is None
    sel._region = (100, 50, 300, 150)
    # scale 1.0：canvas 坐标直接 + x 偏移
    sel._scale_x = 1.0
    sel._scale_y = 1.0
    assert sel._to_virtual_region() == (-1820, 50, 200, 100)
    # scale 2.0：先乘 scale 再加偏移
    sel._scale_x = 2.0
    sel._scale_y = 2.0
    assert sel._to_virtual_region() == (-1720, 100, 400, 200)


def test_calc_region_normalized(sel_env):
    sel = _make(sel_env, x=-1920, width=3840, height=1080)
    # 从右往左拖拽，应归一化
    r = sel._calc_region(500, 300, 100, 100)
    # 虚拟桌面: (500-1920, 300) = (-1420, 300), (100-1920, 100) = (-1820, 100)
    # 归一化: left=-1820, top=100, width=400, height=200
    assert r == (-1820, 100, 400, 200)


def test_calc_region_min_size(sel_env):
    sel = _make(sel_env, width=1920, height=1080)
    # 点击太小（< 5px）视为无效，返回 None
    assert sel._calc_region(100, 100, 101, 101) is None


def test_normalize(sel_env):
    sel = _make(sel_env, width=1920, height=1080)
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


def test_handle_coords(sel_env):
    sel = _make(sel_env, width=1920, height=1080)
    hc = sel._handle_coords(100, 100, 300, 200)
    assert hc["nw"] == (100, 100)
    assert hc["n"] == (200, 100)
    assert hc["ne"] == (300, 100)
    assert hc["e"] == (300, 150)
    assert hc["se"] == (300, 200)
    assert hc["s"] == (200, 200)
    assert hc["sw"] == (100, 200)
    assert hc["w"] == (100, 150)


def test_hit_test(sel_env):
    sel = _make(sel_env, width=1920, height=1080)
    sel._mode = "adjust"
    sel._region = (100, 100, 300, 200)
    assert sel._hit_test(100, 100) == "nw"
    assert sel._hit_test(200, 100) == "n"
    assert sel._hit_test(300, 200) == "se"
    assert sel._hit_test(200, 150) is None
    assert sel._hit_test(400, 300) is None


def test_select_to_adjust_flow(sel_env):
    """模拟 select → adjust 流程。"""
    sel = _make(sel_env, width=1920, height=1080)
    assert sel._mode == "select"

    sel.mousePressEvent(Event(100, 100))
    assert sel.start == (100, 100)

    sel.mouseMoveEvent(Event(300, 200))
    assert sel._mode == "adjust"
    assert sel._region == (100, 100, 300, 200)

    sel.mouseReleaseEvent(Event(300, 200))
    assert sel._mode == "adjust"

    # 拖动 nw 手柄
    sel.mousePressEvent(Event(100, 100))
    assert sel._drag_op == ("nw", "resize")
    sel.mouseMoveEvent(Event(50, 50))
    assert sel._region == (50, 50, 300, 200)
    sel.mouseReleaseEvent(Event(50, 50))

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
    assert sel.result == (vx_l + sel.origin_x, vx_t + sel.origin_y,
                          vx_r - vx_l, vx_b - vx_t)


def test_4k_full_screen_selection(sel_env):
    """4K 屏从左上拖到右下，选区应 snap 到屏幕边缘。

    回归测试：鼠标事件坐标范围是 [0, _actual_w-1]，拖到边缘时如果
    不 snap，选区永远差 1px 选不满。
    """
    sel = _make(sel_env, width=3840, height=2160)
    W, H = sel._actual_w, sel._actual_h

    # 按下 (10, 10)，拖到右边缘 (W-1, H-1)
    sel.mousePressEvent(Event(10, 10))
    sel.mouseMoveEvent(Event(W - 1, H - 1))
    # 右边缘和下边缘应 snap 到 W/H
    assert sel._region[2] == W, f"右边缘没到: {sel._region[2]} != {W}"
    assert sel._region[3] == H, f"下边缘没到: {sel._region[3]} != {H}"
    assert sel._region[0] == 10
    assert sel._region[1] == 10

    # 反向：从右上 (W-1, H-1) 拖到左下 (10, 10)，也应选满
    sel2 = _make(sel_env, width=3840, height=2160)
    W2, H2 = sel2._actual_w, sel2._actual_h
    sel2.mousePressEvent(Event(W2 - 1, H2 - 1))
    sel2.mouseMoveEvent(Event(10, 10))
    assert sel2._region == (10, 10, W2, H2), f"反向选满失败: {sel2._region}"

    # 中间区域不触发 snap
    sel3 = _make(sel_env, width=3840, height=2160)
    sel3.mousePressEvent(Event(100, 100))
    sel3.mouseMoveEvent(Event(500, 300))
    assert sel3._region == (100, 100, 500, 300)


def test_drag_frame_cost_bounded(sel_env):
    """每帧拖动只允许一次窗口移动，工具条尺寸测量必须有界。

    回归测试：旧实现每次 B1-Motion 都触发工具条同步布局计算
    （winfo_* 强制 layout），是 5760x2160 虚拟桌面拖动卡顿的主要根因。
    本测试断言 100 帧拖动期间 adjustSize（布局测量）调用次数有界，
    确保工具条尺寸只在首次显示时测量，后续帧只走 setGeometry 移动。
    """
    sel = _make(sel_env, width=3840, height=2160)

    count = {"adjust": 0}
    real_adjust = sel.tb_win.adjustSize

    def counting_adjust(*a, **kw):
        count["adjust"] += 1
        return real_adjust(*a, **kw)

    sel.tb_win.adjustSize = counting_adjust

    # 模拟 100 帧拖动（框选阶段）
    for i in range(100):
        x = 100 + i
        y = 200 + (i // 2)
        ev = Event(x, y)
        if i == 0:
            sel.mousePressEvent(ev)
        else:
            sel.mouseMoveEvent(ev)

    # 100 帧拖动，尺寸测量应该只发生在首次显示（≤ 2 次容差）
    assert count["adjust"] <= 2, (
        f"100 帧拖动 adjustSize 调用 {count['adjust']} 次，"
        f"工具条布局计算未优化"
    )


def test_brush_stroke_converts_region_pixmap_only_twice(sel_env, monkeypatch):
    """画笔 stroke：整图 PIL→QPixmap 转换只在起笔/收笔各一次，不随 mouseMove。

    回归：旧实现每个 B1-Motion 都把整张选区图重转一次 QPixmap
    （2000x1200 选区 ≈ 每帧 9MB+ 拷贝），是画笔卡顿的主要来源。
    现在：起笔整图转换一次作底图，拖动中只转线段包围盒小 patch，
    收笔后再整图转换一次。
    """
    full = Image.new("RGB", (300, 300), (255, 255, 255))
    sel = _make(sel_env, width=300, height=300, full_img=full)
    sel._mode = "adjust"
    sel._region = (10, 10, 290, 290)
    sel._init_region_img()
    sel.tool = "brush"
    sel.color = "#000000"
    sel.width_attr = 3

    count = {"n": 0}
    real = sel_mod._pil_to_qpixmap
    region_size = sel._region_img.size  # 280x280

    def counting(img):
        # 只统计整图（选区尺寸）转换；stroke 中的局部 patch 小转换不计
        if img.size == region_size:
            count["n"] += 1
        return real(img)

    monkeypatch.setattr(sel_mod, "_pil_to_qpixmap", counting)

    sel.mousePressEvent(Event(20, 20))
    for i in range(1, 50):
        sel.mouseMoveEvent(Event(20 + i, 20 + i))
    sel.mouseReleaseEvent(Event(69, 69))
    assert count["n"] == 2, (
        f"画笔 stroke 触发 {count['n']} 次整图转换（期望 2：起笔 + 收笔），"
        f"未实现 stroke 局部 patch 增量渲染"
    )


def test_brush_patch_visible_during_stroke(sel_env):
    """画笔拖动中（move 后、release 前）渲染已可见线段（局部 patch 合成）。"""
    full = Image.new("RGB", (100, 100), (255, 255, 255))
    sel = _make(sel_env, width=100, height=100, full_img=full)
    sel._mode = "adjust"
    sel._region = (10, 10, 90, 90)
    sel._init_region_img()
    sel.tool = "brush"
    sel.color = "#000000"
    sel.width_attr = 3

    sel.mousePressEvent(Event(20, 20))
    sel.mouseMoveEvent(Event(60, 60))
    # stroke 进行中（未 release）：渲染必须已显示线段，不能等收笔整图转换
    assert _close(_rgb(_render_image(sel), 40, 40), (0, 0, 0), 30), \
        "画笔拖动中段线段未在渲染中显示"
    sel.mouseReleaseEvent(Event(60, 60))
    assert _close(_rgb(_render_image(sel), 40, 40), (0, 0, 0), 30)


def test_handle_resize_does_not_convert_region_per_frame(sel_env, monkeypatch):
    """手柄拖动缩放：期间不做整图 PIL→QPixmap 转换（底图透出）。

    回归：旧实现 resize 每个 B1-Motion 都重裁 + 整图转换。
    无标注状态选区内容必然等于定格底图 crop，直接底图透出
    （与移动路径一致），整图转换推迟到落笔标注时。
    """
    full = Image.new("RGB", (300, 300), (255, 255, 255))
    sel = _make(sel_env, width=300, height=300, full_img=full)
    sel._mode = "adjust"
    sel._region = (10, 10, 150, 150)
    sel._init_region_img()

    count = {"n": 0}
    real = sel_mod._pil_to_qpixmap

    def counting(img):
        count["n"] += 1
        return real(img)

    monkeypatch.setattr(sel_mod, "_pil_to_qpixmap", counting)

    sel.mousePressEvent(Event(150, 150))  # se 手柄
    for i in range(1, 30):
        sel.mouseMoveEvent(Event(150 + i, 150 + i))
    sel.mouseReleaseEvent(Event(179, 179))
    assert count["n"] == 0, (
        f"resize 拖动触发 {count['n']} 次整图转换（期望 0，定格底图透出）"
    )


def test_handle_resize_expanded_area_shows_base(sel_env):
    """手柄拖大放选区后，新露出区域必须显示定格底图内容（透出路径正确）。"""
    full = Image.new("RGB", (300, 300), (255, 255, 255))
    ImageDraw.Draw(full).rectangle([150, 150, 299, 299], fill=(200, 30, 30))  # 右下红色块
    sel = _make(sel_env, width=300, height=300, full_img=full)
    sel._mode = "adjust"
    sel._region = (10, 10, 150, 150)
    sel._init_region_img()

    sel.mousePressEvent(Event(150, 150))
    sel.mouseMoveEvent(Event(220, 220))
    sel.mouseReleaseEvent(Event(220, 220))

    # 窗口坐标 (200, 200) 在新扩出的选区内，应为底图红色块
    assert _close(_rgb(_render_image(sel), 200, 200), (200, 30, 30), 10)


def test_rebase_annotations_threshold_binary(sel_env):
    """_rebase_annotations 标注层提取：任意非零差 → 全遮罩（与旧逐像素 lambda 一致）。"""
    full = Image.new("RGB", (40, 40), (255, 255, 255))
    sel = _make(sel_env, width=40, height=40, full_img=full)
    old_base = Image.new("RGB", (40, 40), (255, 255, 255))
    new_base = Image.new("RGB", (40, 40), (0, 0, 255))
    img = Image.new("RGB", (40, 40), (255, 255, 255))
    ImageDraw.Draw(img).rectangle([5, 5, 20, 15], fill=(255, 0, 0))  # 标注
    out = sel._rebase_annotations(img, old_base, new_base)
    assert out.getpixel((10, 10)) == (255, 0, 0)   # 标注区保留原标注
    assert out.getpixel((30, 30)) == (0, 0, 255)   # 非标注区用新底图


def test_crosshair_font_metrics_cached(sel_env):
    """尺寸标签字体度量缓存，框选过程中不每帧重建 QFont/QFontMetrics。"""
    sel = _make(sel_env)
    assert sel._label_font_metrics() is sel._label_font_metrics()


def test_tb_icon_cached(sel_env):
    """_tb_icon 按（绘制函数, 颜色, 尺寸）缓存 QIcon，同一图标不重复渲染。"""
    i1 = sel_mod._tb_icon(sel_mod._paint_icon_rect)
    i2 = sel_mod._tb_icon(sel_mod._paint_icon_rect)
    assert i1 is i2
    i3 = sel_mod._tb_icon(sel_mod._paint_icon_rect, color="#123456")
    assert i3 is not i1


# =================================================================== #
# 工具条按钮
# =================================================================== #

def test_toolbar_has_all_buttons(sel_env):
    """工具条必须包含完整按钮集合。"""
    sel = _make(sel_env, width=1920, height=1080)
    # 工具按钮
    for tool in ("move", "brush", "rect", "ellipse", "arrow", "mosaic", "text"):
        assert tool in sel._tool_btns, f"缺少工具按钮: {tool}"
    # 操作按钮
    for key in ("confirm", "copy", "save", "cancel", "ocr", "undo", "color"):
        assert key in sel._tb_btns, f"缺少操作按钮: {key}"
    # 都是 QToolButton（可点击）
    for b in list(sel._tool_btns.values()) + list(sel._tb_btns.values()):
        assert isinstance(b, QToolButton), f"按钮类型不对: {b}"


def _icon_avg_color(icon, size=20):
    """图标非透明像素的平均 (r, g, b)。"""
    img = icon.pixmap(size, size).toImage()
    rs = gs = bs = n = 0
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if c.alpha() > 8:
                rs += c.red()
                gs += c.green()
                bs += c.blue()
                n += 1
    if n == 0:
        return (0, 0, 0)
    return (rs // n, gs // n, bs // n)


def test_all_toolbar_buttons_use_drawn_icons(sel_env):
    """全部 14 个工具条按钮（7 工具 + 7 操作）都用矢量绘制图标，不用 emoji 文本。

    回归：emoji/特殊符号（🔍 🎨 💾 ↩ ⧉ ✓ ✕ ✥ ✏ 等）依赖字体回退，
    在 28×24 小按钮上模糊或缺字形；改用 QPainter 高倍绘制的 QIcon 后
    任意 DPI 下清晰、风格一致、易识别。
    """
    import selector as s
    sel = _make(sel_env, width=1920, height=1080)
    # 7 个工具按钮
    for tool in s.Selector.TOOLS:
        b = sel._tool_btns[tool]
        assert b.text() == "", f"工具 {tool} 不应使用文本图标"
        assert not b.icon().isNull(), f"工具 {tool} 应有矢量图标"
        assert b.toolButtonStyle() == Qt.ToolButtonIconOnly
    # 7 个操作按钮
    for key in ("color", "undo", "ocr", "copy", "save", "confirm", "cancel"):
        b = sel._tb_btns[key]
        assert b.text() == "", f"操作 {key} 不应使用文本图标"
        assert not b.icon().isNull(), f"操作 {key} 应有矢量图标"
        assert b.toolButtonStyle() == Qt.ToolButtonIconOnly


def test_all_icon_painters_produce_non_null_qicons(sel_env):
    """全部 14 个图标绘制函数都应产出有效非空 QIcon。"""
    import selector as s
    fns = list(s.TOOL_ICON_FNS.values()) + [
        s._paint_icon_ocr, s._paint_icon_color, s._paint_icon_save,
        s._paint_icon_undo, s._paint_icon_copy,
        s._paint_icon_confirm, s._paint_icon_cancel,
    ]
    assert len(fns) == 14, "应有 14 个图标绘制函数"
    for fn in fns:
        icon = s._tb_icon(fn)
        assert not icon.isNull(), f"{fn.__name__} 产出的 QIcon 为空"
        assert icon.availableSizes()


def test_tool_icon_state_colors(sel_env):
    """工具图标随状态换色：选中=蓝、非选中=深色、禁用=浅灰。"""
    sel = _make(sel_env, width=1920, height=1080)
    sel._set_toolbar_enabled(True)  # adjust 模式：启用工具
    # 选中工具（默认 move）→ 蓝色（B 通道最高）
    r, g, b = _icon_avg_color(sel._tool_btns["move"].icon())
    assert b > r and b > g, f"选中工具图标应为蓝色，实际 {(r, g, b)}"
    # 非选中工具 → 深色
    r, g, b = _icon_avg_color(sel._tool_btns["rect"].icon())
    assert r < 90 and g < 90 and b < 90, f"非选中工具图标应为深色，实际 {(r, g, b)}"
    # 禁用（select 模式）→ 浅灰
    sel._set_toolbar_enabled(False)
    r, g, b = _icon_avg_color(sel._tool_btns["move"].icon())
    assert r > 120 and g > 120 and b > 120, f"禁用工具图标应为浅灰，实际 {(r, g, b)}"


def test_select_tool_updates_state(sel_env):
    """点击工具按钮应更新 self.tool。"""
    sel = _make(sel_env, width=1920, height=1080)
    sel._select_tool("arrow")
    assert sel.tool == "arrow"
    # 选中按钮高亮，其他恢复
    assert sel._tool_btns["arrow"].property("active") is True
    assert sel._tool_btns["rect"].property("active") is False


def test_toolbar_raised_again_when_overlay_activated(sel_env):
    """overlay 成为活动窗口时，已显示的工具条必须被重新提到最前。

    回归：点过工具条按钮后（工具条成为前台窗口），再在选区上左键按下时，
    Windows 激活 overlay 并将其提到置顶层（topmost）最前——全屏 overlay 随即
    盖住工具条，表现为"拖送选框时工具栏消失"。
    """
    sel = _make(sel_env, width=200, height=200)
    sel._mode = "adjust"
    sel._region = (10, 10, 100, 100)
    sel._render_all()
    assert sel._tb_visible

    calls = {"n": 0}

    def counting(*a, **kw):
        calls["n"] += 1

    sel.tb_win.raise_ = counting
    handled = sel.eventFilter(sel, QEvent(QEvent.Type.WindowActivate))
    assert handled is False, "事件过滤器不应吞掉激活事件"
    assert calls["n"] == 1, "overlay 被激活时应重新 raise 工具条"

    # 工具条隐藏时不需要 raise
    sel._tb_hide()
    calls["n"] = 0
    sel.eventFilter(sel, QEvent(QEvent.Type.WindowActivate))
    assert calls["n"] == 0

    # 工具条可见时，非激活事件不触发 raise
    sel._tb_show((10, 10, 100, 100), (10, 10, 90, 90))
    assert sel._tb_visible
    sel.eventFilter(sel, QEvent(QEvent.Type.Paint))
    assert calls["n"] == 0


# =================================================================== #
# 标注绘制
# =================================================================== #

def test_brush_annotation_draws_on_region_img(sel_env):
    full = Image.new("RGB", (100, 100), (255, 255, 255))
    sel = _make(sel_env, width=100, height=100, full_img=full)
    sel._mode = "adjust"
    sel._region = (10, 10, 90, 90)
    sel._init_region_img()
    sel.tool = "brush"
    sel.color = "#000000"
    sel.width_attr = 3

    sel.mousePressEvent(Event(20, 20))
    sel.mouseMoveEvent(Event(60, 60))
    sel.mouseReleaseEvent(Event(60, 60))
    # 画笔线条经过 (30, 30)
    assert _close(_rgb(_render_image(sel), 30, 30), (0, 0, 0), 30) or \
        _close(sel._region_img.getpixel((20, 20)), (0, 0, 0), 30)


def test_rect_annotation_draws_on_region_img(sel_env):
    full = Image.new("RGB", (100, 100), (255, 255, 255))
    sel = _make(sel_env, width=100, height=100, full_img=full)
    sel._mode = "adjust"
    sel._region = (10, 10, 90, 90)
    sel._init_region_img()
    sel.tool = "rect"
    sel.color = "#ff0000"
    sel.width_attr = 2

    sel.mousePressEvent(Event(20, 20))
    sel.mouseReleaseEvent(Event(80, 80))
    assert _close(sel._region_img.getpixel((10, 40)), (255, 0, 0), 25)


def test_undo_reverts_annotation(sel_env):
    full = Image.new("RGB", (100, 100), (255, 255, 255))
    sel = _make(sel_env, width=100, height=100, full_img=full)
    sel._mode = "adjust"
    sel._region = (10, 10, 90, 90)
    sel._init_region_img()
    sel.tool = "rect"
    sel.color = "#ff0000"
    sel.width_attr = 2

    sel.mousePressEvent(Event(20, 20))
    sel.mouseReleaseEvent(Event(80, 80))
    sel._undo()
    assert sel._region_img.getpixel((40, 40)) == (255, 255, 255)


def test_undo_renders_last_image(sel_env):
    """撤销后必须重绘选区图（_refresh_region_pixmap 被调用）。

    回归：撤销使 undo 栈变空后最后一张图像（干净选区）不会重绘，
    表现为"撤销没反应"。
    """
    full = Image.new("RGB", (100, 100), (255, 255, 255))
    sel = _make(sel_env, width=100, height=100, full_img=full)
    sel._mode = "adjust"
    sel._region = (10, 10, 90, 90)
    sel._init_region_img()
    sel.tool = "rect"
    sel.color = "#ff0000"
    sel.width_attr = 2

    sel.mousePressEvent(Event(20, 20))
    sel.mouseReleaseEvent(Event(80, 80))
    mock_refresh = MagicMock()
    sel._refresh_region_pixmap = mock_refresh
    sel._undo()
    assert mock_refresh.called


def test_undo_fully_reverts_many_strokes(sel_env):
    """回归：多笔标注全部撤销后，选区图必须精确回到干净底图。

    此前 maxlen=10，画的笔数超过 10 后最旧快照被挤出栈，
    撤销到栈空也回不到干净底图，前几笔标注残留在图上（"撤销没干净"）。
    """
    full = Image.new("RGB", (100, 100), (255, 255, 255))
    sel = _make(sel_env, width=100, height=100, full_img=full)
    sel._mode = "adjust"
    sel._region = (10, 10, 90, 90)
    sel._init_region_img()
    clean = sel._region_img.copy()  # 干净底图
    sel.tool = "rect"
    sel.color = "#ff0000"
    sel.width_attr = 2

    n = 15  # 超过旧上限 10
    for i in range(n):
        # 每笔画在不同位置（选区内、避开 8 个手柄的 6px 热区），避免相互覆盖
        x0 = 20 + (i % 5) * 15
        y0 = 20 + (i // 5) * 20
        sel.mousePressEvent(Event(x0, y0))
        sel.mouseReleaseEvent(Event(x0 + 10, y0 + 10))

    # 画满 n 笔后撤销栈应完整保留 n 份快照（不被挤出）
    assert len(sel.undo) == n

    # 撤销 n 次 → 选区图必须精确等于干净底图
    for _ in range(n):
        sel._undo()
    assert sel._region_img.tobytes() == clean.tobytes()


def test_mosaic_annotation(sel_env):
    full = Image.new("RGB", (100, 100), (255, 255, 255))
    d = ImageDraw.Draw(full)
    d.line([(10, 10), (90, 90)], fill=(0, 0, 0), width=5)
    sel = _make(sel_env, width=100, height=100, full_img=full)
    sel._mode = "adjust"
    sel._region = (0, 0, 100, 100)
    sel._init_region_img()
    sel.tool = "mosaic"
    sel.width_attr = 8

    sel.mousePressEvent(Event(10, 10))
    sel.mouseReleaseEvent(Event(90, 90))
    has_mid = any(sel._region_img.getpixel((x, y)) not in ((0, 0, 0), (255, 255, 255))
                  for x in range(0, 100, 5) for y in range(0, 100, 5))
    assert has_mid


def test_text_annotation(sel_env):
    full = Image.new("RGB", (100, 100), (255, 255, 255))
    sel = _make(sel_env, width=100, height=100, full_img=full)
    sel._mode = "adjust"
    sel._region = (10, 10, 90, 90)
    sel._init_region_img()
    sel.tool = "text"
    sel.color = "#000000"

    with patch.object(sel, "_input_text_dialog") as mock_dialog:
        mock_dialog.return_value = "Hi"
        sel._input_text(20, 20)
    has_dark = any(sel._region_img.getpixel((x, y)) != (255, 255, 255)
                   for x in range(15, 45) for y in range(15, 35))
    assert has_dark


# =================================================================== #
# 渲染
# =================================================================== #

def test_render_all_in_adjust_mode_reuses_selection_masks(sel_env):
    """adjust 模式下暗化遮罩由 paintEvent 按 _region 状态绘制：
    选区外四周暗化，选区内保留原图亮度，且底图/选区图只转换一次。"""
    full = Image.new("RGB", (200, 200), (255, 255, 255))
    sel = _make(sel_env, full_img=full)
    sel._mode = "adjust"
    sel._region = (50, 50, 150, 150)
    sel._init_region_img()
    base_before = sel._base_pixmap
    sel._render_all()
    # 底图 pixmap 复用（不每帧重建）
    assert sel._base_pixmap is base_before
    assert sel._region_pixmap is not None

    img = _render_image(sel)
    # 选区外（左上）暗化 ≈ 127 灰
    assert _close(_rgb(img, 10, 10), (127, 127, 127), 10)
    # 选区内保持原图亮度
    assert _close(_rgb(img, 100, 100), (255, 255, 255), 10)


def test_border_double_line_and_handle_outline(sel_env):
    """选框双线（白外线 + 深色内线）且手柄白底 + 深色描边。

    回归：选框与手柄曾为纯白，白色截图背景上完全不可见。
    """
    full = Image.new("RGB", (100, 100), (255, 255, 255))  # 白色背景
    sel = _make(sel_env, width=100, height=100, full_img=full)
    sel._mode = "adjust"
    sel._region = (10, 10, 90, 90)
    sel._init_region_img()
    sel._render_all()

    assert BORDER_INNER != "white"
    img = _render_image(sel)

    # 白色外线（双线的外线；白色背景上靠深色内线可见）
    assert _close(_rgb(img, 30, 10), (255, 255, 255), 10)
    # 深色内线位于外线内侧 2px（避开 8 个手柄的覆盖范围）
    assert _close(_rgb(img, 30, 12), (0x4e, 0x59, 0x69), 40)
    # 手柄：白底
    assert _close(_rgb(img, 4, 4), (255, 255, 255), 10)
    # 手柄深色描边（nw 手柄底边，避开暗化区误判）
    assert _close(_rgb(img, 10, 21), (0x4e, 0x59, 0x69), 60)


def test_border_drawn_during_initial_selection_no_base(sel_env):
    """初始框选（尚未定格、无底图 pixmap）时就应画出选区边框线。

    回归：PySide6 版曾把 adjust 渲染整体放在 ``_base_pixmap is not None``
    分支内，而首次框选阶段 ``_base_pixmap`` 为 None（松手才定格），导致
    画框拖动过程中完全看不到边框线，只有十字准星。
    """
    sel = _make(sel_env, width=200, height=200, full_img=None,
                capture_screen=None)
    # 按下 + 拖动 → 进入框选（select→adjust 过渡）
    sel.mousePressEvent(Event(20, 20))
    sel.mouseMoveEvent(Event(120, 120))

    # 此时仍是框选阶段，尚未定格
    assert sel._mode == "adjust"
    assert sel._region is not None
    assert sel._base_pixmap is None
    assert sel._dragging_box is True

    img = _render_image(sel)
    l, t, r, b = sel._region
    # 顶边白色外线（避开十字准星 x=120 / y=120）
    assert _close(_rgb(img, (l + r) // 2, t), (255, 255, 255), 10)
    # 左边白色外线
    assert _close(_rgb(img, l, (t + b) // 2), (255, 255, 255), 10)
    # 框内仍为暗化黑底（无底图时），而非选区图
    assert _close(_rgb(img, (l + r) // 2, (t + b) // 2), (0, 0, 0), 10)


def test_render_all_in_select_mode_no_masks(sel_env):
    """select 模式下 _render_all 不应渲染选区图。"""
    full = Image.new("RGB", (200, 200), (255, 255, 255))
    sel = _make(sel_env, full_img=full)
    sel._mode = "select"
    sel._render_all()
    assert sel._region_pixmap is None
    assert not sel._tb_visible


def test_selection_masks_cover_all_outside_areas(sel_env):
    """选区外四周都应保持暗化，只有框内区域高亮；
    移动选区时遮罩临时隐藏（透出定格底图）。"""
    full = Image.new("RGB", (100, 100), (255, 255, 255))
    sel = _make(sel_env, width=100, height=100, full_img=full)
    sel._actual_w = 100
    sel._actual_h = 100
    sel._mode = "adjust"
    sel._region = (20, 30, 70, 80)
    sel._init_region_img()
    sel._render_all()

    img = _render_image(sel)
    # 4 块选区外区域均暗化
    for px in ((5, 5), (90, 50), (5, 90), (45, 95)):
        assert _close(_rgb(img, *px), (127, 127, 127), 10), f"{px} 未暗化"
    # 选区内保持原图亮度
    assert _close(_rgb(img, 45, 55), (255, 255, 255), 10)

    # 移动选区时遮罩隐藏 → 选区外透出原图亮度
    sel._masks_hidden = True
    sel.update()
    img2 = _render_image(sel)
    assert _close(_rgb(img2, 5, 5), (255, 255, 255), 10)


# =================================================================== #
# 工具条启用/禁用
# =================================================================== #

def test_toolbar_disabled_in_select_mode(sel_env):
    """select 模式下工具按钮应禁用（灰色）。"""
    sel = _make(sel_env, width=1920, height=1080)
    assert sel._mode == "select"
    # 构造后应处于禁用状态
    for tool, b in sel._tool_btns.items():
        assert not b.isEnabled(), f"工具 {tool} 在 select 模式未禁用"


def test_toolbar_enabled_in_adjust_mode(sel_env):
    """进入 adjust 模式后工具按钮应启用。"""
    sel = _make(sel_env, width=1920, height=1080)
    # 模拟进入 adjust
    sel.mousePressEvent(Event(100, 100))
    assert sel._mode == "adjust"
    for tool, b in sel._tool_btns.items():
        assert b.isEnabled(), f"工具 {tool} 在 adjust 模式未启用"


# =================================================================== #
# 选区锁定（画过标注后手柄不可拖动）
# =================================================================== #

def test_hit_test_returns_none_after_annotation(sel_env):
    """画过标注（undo 栈非空）后，_hit_test 应返回 None，手柄不可拖动。"""
    full = Image.new("RGB", (100, 100), (255, 255, 255))
    sel = _make(sel_env, width=100, height=100, full_img=full)
    sel._mode = "adjust"
    sel._region = (10, 10, 90, 90)
    sel._init_region_img()

    # 未画标注前，手柄可命中
    assert sel._hit_test(10, 10) == "nw"

    # 画一笔标注（undo.push 被调用，undo 栈非空）
    sel.tool = "brush"
    sel.color = "#000000"
    sel.width_attr = 3
    sel.mousePressEvent(Event(20, 20))
    sel.mouseReleaseEvent(Event(20, 20))

    # 画过标注后，手柄不可命中
    assert sel._hit_test(10, 10) is None, "画过标注后手柄应锁定（_hit_test 返回 None）"


def test_hit_test_still_works_before_annotation(sel_env):
    """未画标注时，_hit_test 正常工作。"""
    full = Image.new("RGB", (100, 100), (255, 255, 255))
    sel = _make(sel_env, width=100, height=100, full_img=full)
    sel._mode = "adjust"
    sel._region = (10, 10, 90, 90)
    sel._init_region_img()
    # 未画标注，undo 栈为空
    assert sel.undo._stack == []
    assert sel._hit_test(10, 10) == "nw"
    assert sel._hit_test(90, 10) == "ne"
    assert sel._hit_test(10, 90) == "sw"
    assert sel._hit_test(50, 50) is None


def test_handle_drag_blocked_after_annotation(sel_env):
    """画过标注后，拖动手柄不应改变选区。"""
    full = Image.new("RGB", (100, 100), (255, 255, 255))
    sel = _make(sel_env, width=100, height=100, full_img=full)
    sel._mode = "adjust"
    sel._region = (10, 10, 90, 90)
    sel._init_region_img()

    # 画一笔标注
    sel.tool = "brush"
    sel.color = "#000000"
    sel.width_attr = 3
    sel.mousePressEvent(Event(20, 20))
    sel.mouseReleaseEvent(Event(20, 20))

    # 尝试拖 nw 手柄
    sel.mousePressEvent(Event(10, 10))
    assert sel._drag_op is None, "画过标注后不应设置 _drag_op（手柄锁定）"


# =================================================================== #
# 工具切换不应抢焦点 / 标注后工具条保持在最前
# =================================================================== #

def test_handle_unlocked_after_undo(sel_env):
    """撤销后 undo 栈清空，手柄应重新可拖动。"""
    full = Image.new("RGB", (100, 100), (255, 255, 255))
    sel = _make(sel_env, width=100, height=100, full_img=full)
    sel._mode = "adjust"
    sel._region = (10, 10, 90, 90)
    sel._init_region_img()

    # 画一笔标注 → undo 栈非空 → 手柄锁定
    sel.tool = "brush"
    sel.color = "#000000"
    sel.width_attr = 3
    sel.mousePressEvent(Event(20, 20))
    sel.mouseReleaseEvent(Event(20, 20))
    assert sel._hit_test(10, 10) is None

    # 撤销 → undo 栈清空 → 手柄解锁
    sel._undo()
    assert sel._hit_test(10, 10) == "nw"


# =================================================================== #
# 手柄 resize 拖到尺寸 < MIN_SIZE 时不应崩溃
# =================================================================== #

def test_adjust_region_handles_none_region(sel_env):
    """拖动手柄使选区尺寸 < MIN_SIZE 时，_normalize 返回 None，
    _region 被置空。_adjust_region 不应因解包 None 而崩溃。
    """
    full = Image.new("RGB", (100, 100), (255, 255, 255))
    sel = _make(sel_env, width=100, height=100, full_img=full)
    sel._mode = "adjust"
    sel._region = (10, 10, 90, 90)
    sel._init_region_img()

    # 拖 nw 手柄向右下，使选区宽 = 10-90 = -80 → 尺寸 < MIN_SIZE → _region = None
    sel.mousePressEvent(Event(10, 10))
    assert sel._drag_op is not None
    sel.mouseMoveEvent(Event(90, 90))

    assert sel._region is None
    # mouseMoveEvent → _adjust_region 不应抛异常（已加 None 防御）


def test_adjust_region_direct_none_no_crash(sel_env):
    """_region 为 None 时直接调用 _adjust_region 不应崩溃。"""
    full = Image.new("RGB", (100, 100), (255, 255, 255))
    sel = _make(sel_env, width=100, height=100, full_img=full)
    sel._mode = "adjust"
    sel._region = None
    # 不应抛 TypeError
    sel._adjust_region()


# =================================================================== #
# 工具条 Z 序 / 定位
# =================================================================== #

def test_toolbar_topmost_after_annotation(sel_env):
    """标注完成后，工具条应保持置顶（topmost 标志 + raise_ 调整 Z 序）。"""
    full = Image.new("RGB", (100, 100), (255, 255, 255))
    sel = _make(sel_env, width=100, height=100, full_img=full)
    sel._mode = "adjust"
    sel._region = (10, 10, 90, 90)
    sel._init_region_img()

    # 画一笔标注
    sel.tool = "brush"
    sel.color = "#000000"
    sel.width_attr = 3
    with patch.object(sel.tb_win, "raise_", wraps=sel.tb_win.raise_) as raise_spy:
        sel.mousePressEvent(Event(20, 20))
        sel.mouseReleaseEvent(Event(20, 20))

    raise_spy.assert_called()
    assert Qt.WindowStaysOnTopHint in sel.tb_win.windowFlags()


def test_select_tool_sets_topmost_but_no_lift(sel_env):
    """切换工具应保持工具条置顶，但不抢键盘焦点（不 activateWindow）。"""
    sel = _make(sel_env, width=1920, height=1080)
    with patch.object(sel.tb_win, "activateWindow",
                      wraps=sel.tb_win.activateWindow) as aw:
        sel._select_tool("rect")
    # 切换工具不应激活窗口（避免抢焦点）
    aw.assert_not_called()
    # topmost 标志已含在窗口标志中
    assert Qt.WindowStaysOnTopHint in sel.tb_win.windowFlags()


def test_toolbar_stays_above_overlay_and_inside_screen_while_moving(sel_env):
    sel = _make(sel_env, x=-100, width=1000, height=800)
    sel._actual_w, sel._actual_h = 1000, 800
    sel._tb_visible = True
    sel._tb_size = (180, 40)

    sel._tb_show((300, 760, 500, 800), (200, 760, 200, 40))

    assert sel.tb_win.geometry() == QRect(200, 716, 180, 40)
    assert sel._tb_pos == (200, 716, 180, 40)
    assert Qt.WindowStaysOnTopHint in sel.tb_win.windowFlags()


def test_toolbar_first_show_at_final_geometry(sel_env):
    """首次显示工具栏时必须直接落在最终位置（隐藏态测量尺寸后一次定位），
    避免窗口在默认位置以空内容闪现一帧（白底闪烁）。

    回归：旧实现先在默认位置显示空窗口，下一帧才移动到选区下方。
    """
    sel = _make(sel_env, width=1000, height=800)
    sel._actual_w, sel._actual_h = 1000, 800

    geo_calls = []
    real_set_geometry = sel.tb_win.setGeometry

    def spy_set_geometry(*a, **kw):
        geo_calls.append(a[:4] if len(a) >= 4 else (
            a[0].x(), a[0].y(), a[0].width(), a[0].height()))
        return real_set_geometry(*a, **kw)

    sel.tb_win.setGeometry = spy_set_geometry

    sel._tb_show((300, 760, 500, 800), (200, 760, 200, 40))

    tb_w, tb_h = sel._tb_size
    # 工具条必须已显示，且尺寸已测量
    assert sel.tb_win.isVisible()
    assert tb_w > 0 and tb_h > 0
    # 唯一一次定位就是最终位置（选区下方放不下 → 翻到选区顶部上方）
    final = (300, 760 - tb_h - 4, tb_w, tb_h)
    assert geo_calls == [final], f"首次显示未直接定位到最终位置: {geo_calls}"
    assert sel._tb_pos == (300, 760 - tb_h - 4, tb_w, tb_h)


# =================================================================== #
# OCR
# =================================================================== #

def test_ocr_missing_engine_shows_critical(sel_env):
    """未安装 OCR 引擎时弹错误提示，不抛异常。"""
    full = Image.new("RGB", (100, 100), (255, 255, 255))
    sel = _make(sel_env, width=100, height=100, full_img=full)
    sel._mode = "adjust"
    sel._region = (10, 10, 90, 90)
    sel._init_region_img()

    with patch.object(sel_env, "get_ocr_engine", return_value=None), \
         patch.object(sel_env, "QMessageBox") as mb:
        sel._ocr()
    mb.critical.assert_called_once()


def test_ocr_runs_in_thread_and_posts_result_on_main_thread(sel_env, qt_app):
    """OCR 在工作线程执行，结果通过跨线程信号投递回主线程弹窗逻辑。"""
    full = Image.new("RGB", (100, 100), (255, 255, 255))
    sel = _make(sel_env, width=100, height=100, full_img=full)
    sel._mode = "adjust"
    sel._region = (10, 10, 90, 90)
    sel._init_region_img()

    def fake_engine(arr):
        return [[[[0, 0], [50, 0], [50, 10], [0, 10]], "hello", 0.99]]

    with patch.object(sel_env, "get_ocr_engine", return_value=fake_engine), \
         patch.object(sel, "_show_ocr_result") as show:
        sel._ocr()
        # 泵事件循环，等待工作线程的结果信号投递到主线程
        deadline = time.time() + 5
        while not show.called and time.time() < deadline:
            qt_app.processEvents()
            time.sleep(0.02)

    assert show.called, "OCR 结果未投递回主线程"
    assert show.call_args[0][0] == "hello"


def test_ocr_result_dialog_copy_all(sel_env):
    """OCR 结果弹窗：复制全部成功时接受对话框并写入剪贴板。"""
    full = Image.new("RGB", (100, 100), (255, 255, 255))
    sel = _make(sel_env, width=100, height=100, full_img=full)

    from selector import _OcrResultDialog
    from PySide6.QtWidgets import QPushButton

    dlg = _OcrResultDialog("line1\nline2", sel)
    with patch("capture.copy_text_to_clipboard", return_value=True) as cc:
        btn = next(b for b in dlg.findChildren(QPushButton) if b.text() == "复制全部")
        btn.click()
    from PySide6.QtWidgets import QDialog
    assert dlg.result() == QDialog.Accepted
    cc.assert_called_once_with("line1\nline2")


# =================================================================== #
# 工具条图标与顺序（对齐参考设计截图：Lucide 风格细线条图标）
# =================================================================== #

def _icon_lum(icon, size=20):
    """图标取点函数：lum(fx, fy) 返回逻辑位置 (0..1) 处墨迹灰度；
    透明（无墨迹）处按 255（白）计。"""
    img = icon.pixmap(size, size).toImage()
    W, H = img.width(), img.height()

    def lum(fx, fy):
        x = max(0, min(W - 1, int(round(fx * (W - 1)))))
        y = max(0, min(H - 1, int(round(fy * (H - 1)))))
        c = img.pixelColor(x, y)
        return c.lightness() if c.alpha() > 8 else 255

    return lum


def test_toolbar_tool_order_matches_reference(sel_env):
    """工具按钮顺序对齐参考设计：move → 矩形 → 圆 → 箭头 → 画笔 → 文字 → 马赛克。"""
    sel = _make(sel_env, width=1920, height=1080)
    assert list(sel_mod.Selector.TOOLS) == [
        "move", "rect", "ellipse", "arrow", "brush", "text", "mosaic"]
    # 按钮创建顺序 = 工具条从左到右顺序
    btns = sel.tb_win.findChildren(QToolButton)
    for i, tool in enumerate(sel_mod.Selector.TOOLS):
        assert btns[i] is sel._tool_btns[tool], f"工具条第 {i} 位应为 {tool} 工具"


def test_toolbar_action_tail_order(sel_env):
    """尾部动作按钮顺序对齐参考设计：复制 → 保存(下载) → 取消(红 X) → 确认(绿 ✓)。"""
    sel = _make(sel_env, width=1920, height=1080)
    btns = sel.tb_win.findChildren(QToolButton)
    idx = {b: i for i, b in enumerate(btns)}
    order = [idx[sel._tb_btns[k]] for k in ("copy", "save", "cancel", "confirm")]
    assert order == sorted(order), f"尾部顺序应为 复制→保存→取消→确认，实际索引 {order}"


def test_mosaic_icon_frame_with_x_cells(sel_env):
    """马赛克图标 = 圆角方框 + X 形 5 格填充（四角 + 中心），边缘中点不填充。"""
    lum = _icon_lum(sel_mod._tb_icon(sel_mod._paint_icon_mosaic))
    for fx, fy in ((0.3, 0.3), (0.7, 0.3), (0.5, 0.5), (0.3, 0.7), (0.7, 0.7)):
        assert lum(fx, fy) < 100, f"马赛克图标 ({fx}, {fy}) 处应有填充格"
    for fx, fy in ((0.5, 0.3), (0.3, 0.5), (0.7, 0.5), (0.5, 0.7)):
        assert lum(fx, fy) > 150, f"马赛克图标边缘中点 ({fx}, {fy}) 不应填充"


def test_ocr_icon_is_scan_text(sel_env):
    """OCR 图标 = 扫描文本样式（参考设计）：四个 L 形角框 + 中央 3 条文本行。"""
    lum = _icon_lum(sel_mod._tb_icon(sel_mod._paint_icon_ocr))
    # 四角框角点
    for fx, fy in ((0.24, 0.24), (0.76, 0.24), (0.76, 0.76), (0.24, 0.76)):
        assert lum(fx, fy) < 100, f"OCR 图标 ({fx}, {fy}) 处应有角框"
    # 中央文本行
    for fx, fy in ((0.5, 0.40), (0.5, 0.50), (0.45, 0.60)):
        assert lum(fx, fy) < 100, f"OCR 图标 ({fx}, {fy}) 处应有文本行"
    # 角框之间的空隙
    assert lum(0.5, 0.24) > 150, "OCR 图标顶边中部应为角框空隙"
    # 顶角框与文本行之间的空白
    assert lum(0.5, 0.30) > 150, "OCR 图标角框与文本行之间应为空白"


def test_save_icon_is_download(sel_env):
    """保存图标 = 下载样式（向下箭头 + 底部托盘），不再是软盘。"""
    lum = _icon_lum(sel_mod._tb_icon(sel_mod._paint_icon_save))
    assert lum(0.5, 0.35) < 100, "保存图标应有垂直箭杆"
    assert lum(0.5, 0.61) < 100, "保存图标箭头尖端应朝下"
    assert lum(0.5, 0.78) < 100, "保存图标应有底部托盘"
    assert lum(0.25, 0.5) > 150, "保存图标左侧应为空白"


def test_undo_icon_is_hook_arrow(sel_env):
    """撤销图标 = 钩状回转箭头（左指箭头 + C 形钩），不是整圈回旋。"""
    lum = _icon_lum(sel_mod._tb_icon(sel_mod._paint_icon_undo))
    assert lum(0.28, 0.38) < 100, "撤销图标箭头应指向左"
    assert lum(0.5, 0.68) < 100, "撤销图标底部应有钩形弧线"
    assert lum(0.78, 0.3) > 150, "撤销图标右上应为空白"


def test_arrow_icon_has_open_arrowhead(sel_env):
    """箭头图标 = 斜线杆 + 尖端直角开口箭头（Lucide arrow-up-right 风格）。"""
    lum = _icon_lum(sel_mod._tb_icon(sel_mod._paint_icon_arrow))
    assert lum(0.72, 0.28) < 100, "箭头图标尖端应在右上"
    assert lum(0.55, 0.28) < 100, "箭头图标应有水平翼"
    assert lum(0.72, 0.45) < 100, "箭头图标应有垂直翼"
    assert lum(0.45, 0.55) < 100, "箭头图标应有斜向线杆"


def test_brush_icon_has_squiggle(sel_env):
    """画笔图标 = 45° 铅笔 + 左下波浪线（参考设计带下划波浪的钢笔样式）。"""
    lum = _icon_lum(sel_mod._tb_icon(sel_mod._paint_icon_brush))
    assert lum(0.55, 0.45) < 100, "画笔图标应有铅笔"
    assert lum(0.35, 0.75) < 100, "画笔图标左下应有波浪线"


def test_text_icon_has_serifs(sel_env):
    """文字图标 = 衬线 T（顶横 + 两侧衬线 + 底部衬线）。"""
    lum = _icon_lum(sel_mod._tb_icon(sel_mod._paint_icon_text))
    assert lum(0.5, 0.28) < 100, "文字图标应有顶横"
    assert lum(0.5, 0.5) < 100, "文字图标应有竖杆"
    assert lum(0.5, 0.72) < 100, "文字图标应有底部衬线"
    # 顶横两端下探的衬线（无衬线 T 在此处为空白）
    assert lum(0.31, 0.33) < 100, "文字图标左端应有衬线"
    assert lum(0.69, 0.33) < 100, "文字图标右端应有衬线"
    assert lum(0.68, 0.5) > 150, "文字图标右侧应为空白"


def test_rect_ellipse_icons_are_square_proportion(sel_env):
    """矩形 / 圆形图标墨迹包围盒应接近正方形（参考设计为方 / 正圆，
    而非旧的扁矩形 / 扁椭圆）。"""
    def bbox_ratio(fn):
        img = sel_mod._tb_icon(fn).pixmap(20, 20).toImage()
        minx = miny = 99
        maxx = maxy = -1
        for y in range(img.height()):
            for x in range(img.width()):
                if img.pixelColor(x, y).alpha() > 8:
                    minx, maxx = min(minx, x), max(maxx, x)
                    miny, maxy = min(miny, y), max(maxy, y)
        return (maxx - minx + 1) / (maxy - miny + 1)

    assert 0.9 <= bbox_ratio(sel_mod._paint_icon_rect) <= 1.1, "矩形图标应为方形比例"
    assert 0.9 <= bbox_ratio(sel_mod._paint_icon_ellipse) <= 1.1, "圆形图标应为正圆"
