# 选区后内联标注窗口（Approach B）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 框选完成后打开全屏不透明标注窗口，用户直接在选区上标注（箭头/矩形/画笔/文字/马赛克），标注后确认进 Editor 或复制/保存。

**Architecture:** 两阶段——RegionSelector 覆盖层（透明，框选+调整）→ AnnotationWindow（不透明，全屏截图背景+选区暗化+工具条+标注）。drawing.py 从 editor.py 抽出共享绘制函数。app.py 的 process_region_selection 改为打开 AnnotationWindow。

**Tech Stack:** Python 3, tkinter, PIL (Pillow), mss, ctypes (Win32 API)

**Spec:** `docs/superpowers/specs/2026-11-01-inline-annotation-design.md`

## Global Constraints

- Python + tkinter，与现有代码栈一致
- Windows 优先（现有代码已大量使用 win32 API）
- 共享 Tk 根窗口架构不变（selector / editor / annotation 都是 Toplevel，主循环驱动）
- 选区阶段标注的绘制逻辑复用 editor.py 已有函数，抽成模块级共享函数，避免两份实现
- 全屏截图 full_img 尺寸 = 虚拟桌面尺寸（与 selector 的 `_actual_w/_actual_h` 一致）
- Canvas 坐标 = 虚拟桌面坐标（与 selector 相同映射）
- 半透明遮罩：PIL 预渲染 4 块黑色半透明图（alpha=128），选区变化时重建
- 标注撤销限 10 步，只撤销标注不影响选区
- 调整选区后标注作废：清空标注、从 full_img 重新 crop region_img、清空撤销栈
- 文字工具：点击选区内位置弹输入框，回车后画到 region_img
- 工具条超出屏幕下边缘时放选区上方

## Review Focus

1. **大尺寸全屏截图（5760×2160）的 PhotoImage 转换**：full_img 很大，PhotoImage 转换开销大，但只转一次。验证：标注窗口打开后不卡顿，选区调整时不重建全屏 PhotoImage。
2. **选区外暗化遮罩与选区内明亮区域的边界**：4 块遮罩的坐标计算必须精确，否则选区边缘会有暗化残留或漏光。验证：选区边缘 1px 内无暗化，遮罩完全覆盖选区外。
3. **标注后调整选区导致标注作废**：移动/缩放手柄后标注必须清空、重新 crop、清空撤销栈。验证：调整选区后 region_img 恢复为原始 crop，撤销栈为空，工具条撤销按钮变灰。
4. **多显示器坐标映射**：选区图/遮罩/工具条都基于 canvas 坐标，必须与 selector 的 `_to_virtual` 换算一致。验证：在副屏选区，标注后 region 坐标正确。
5. **文字输入框打开时按 Esc 只关输入框不取消标注窗口**：验证：文字输入框按 Esc 后，标注窗口仍在，可以继续标注。

---

### Task 1: drawing.py 共享绘制函数 + editor.py 改为 import

**Files:**
- Create: `drawing.py`
- Create: `tests/test_drawing.py`
- Modify: `editor.py:113-148`（移除 UndoStack / mosaic 定义，改为 import）
- Modify: `editor.py:821-835`（`_draw_arrow` 改为调用 drawing.draw_arrow）
- Modify: `editor.py:749-795`（`_on_release` 的 rect/ellipse/brush 分支改为调用 drawing 函数）
- Modify: `editor.py:851-853`（`_input_text` 的 commit 改为调用 drawing.draw_text）
- Modify: `tests/test_editor.py`（现有测试应继续通过，验证 `from editor import UndoStack, mosaic` 仍可用）

**Interfaces:**
- Produces:
  - `drawing.py` 模块，含以下函数和类：
    - `draw_arrow(img, start, end, color, width) -> None` — 在 img 上画箭头（含三角箭头），start/end 为 (x, y) 元组，color 为 hex 字符串，width 为 int
    - `draw_rect(img, box, color, width) -> None` — 在 img 上画 outline 矩形，box 为 (x0, y0, x1, y1) 元组（已归一化 x0≤x1, y0≤y1）
    - `draw_ellipse(img, box, color, width) -> None` — 在 img 上画 outline 椭圆
    - `draw_brush(img, points, color, width) -> None` — 在 img 上画折线画笔，points 为 [(x, y), ...] 列表
    - `draw_text(img, pos, text, color, font_size) -> None` — 在 img 上画文字，pos 为 (x, y) 元组，font_size 为 int
    - `mosaic(img, left, top, right, bottom, block=8) -> PIL.Image` — 对 img 的指定区域做马赛克，返回新图（从 editor.py 搬过来，逻辑不变）
    - `UndoStack` 类（从 editor.py 搬过来，maxlen=10，push/pop/clear）

- [ ] **Step 1: Write failing tests for drawing.py**

```python
# tests/test_drawing.py
import pytest
from PIL import Image
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
    img = _blank()
    # 画一条黑线
    from PIL import ImageDraw
    d = ImageDraw.Draw(img)
    d.line([(10, 10), (90, 90)], fill=(0, 0, 0), width=5)
    result = mosaic(img, 0, 0, 100, 100, block=10)
    # 马赛克后，对角线区域应有模糊（非纯黑也非纯白的中间像素）
    has_mid = any(result.getpixel((x, y)) not in ((0, 0, 0), (255, 255, 255))
                  for x in range(0, 100, 5) for y in range(0, 100, 5))
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_drawing.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'drawing'"

- [ ] **Step 3: Create drawing.py**

Create `drawing.py` with the following content. Copy the `mosaic` function and `UndoStack` class from editor.py (lines 113-148) verbatim. For the drawing functions, extract the logic from editor.py's `_draw_arrow` (lines 821-835), `_on_release` rect branch (lines 749-758), ellipse branch (lines 759-770), brush logic (lines 663-667, 716-720), and `_input_text` commit (lines 851-853).

Key signatures:

```python
import math
from PIL import Image, ImageDraw


def draw_arrow(img, start, end, color, width):
    """在 img 上画箭头（线条 + 三角箭头头）。"""
    # 从 editor.py _draw_arrow (lines 821-835) 搬过来，
    # 把 self.image/self.color/self.width 替换为参数


def draw_rect(img, box, color, width):
    """在 img 上画 outline 矩形。box 已归一化 (x0,y0,x1,y1)。"""
    # 从 editor.py _on_release rect 分支 (lines 749-758) 搬过来


def draw_ellipse(img, box, color, width):
    """在 img 上画 outline 椭圆。box 已归一化。"""
    # 从 editor.py _on_release ellipse 分支 (lines 759-770) 搬过来


def draw_brush(img, points, color, width):
    """在 img 上画折线画笔。points 为 [(x, y), ...]。"""
    # 从 editor.py _on_drag brush 逻辑 (lines 716-720) 搬过来，
    # 用 ImageDraw.line(points, fill=color, width=width) 一次画整段


def draw_text(img, pos, text, color, font_size):
    """在 img 上画文字。pos 为 (x, y)，font_size 为 int。"""
    # 从 editor.py _input_text commit (lines 851-853) 搬过来，
    # 用 ImageDraw.text(pos, text, fill=color) + 指定字体


mosaic = ...  # 从 editor.py 搬过来
UndoStack = ...  # 从 editor.py 搬过来
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_drawing.py -v`
Expected: PASS

- [ ] **Step 5: Modify editor.py to import from drawing.py**

In `editor.py`:
- Remove the `UndoStack` class definition (lines 113-129)
- Remove the `mosaic` function definition (lines 132-148)
- Add at the top (after imports): `from drawing import mosaic, UndoStack, draw_arrow, draw_rect, draw_ellipse, draw_brush, draw_text`
- Change `_draw_arrow` (line 821) to: `def _draw_arrow(self, start, end): draw_arrow(self.image, start, end, self.color, self.width)`
- Change `_on_release` rect branch (lines 749-758) to call `draw_rect(self.image, [min(x0,x1), min(y0,y1), max(x0,x1), max(y0,y1)], self.color, self.width)`
- Change `_on_release` ellipse branch (lines 759-770) to call `draw_ellipse(self.image, box, self.color, self.width)`
- Change `_on_drag` brush logic (lines 716-720) to call `draw_brush(self.image, [self._brush_last, pos], self.color, self.width)`
- Change `_input_text` commit (lines 851-853) to call `draw_text(self.image, (x, y), text, self.color, 12)`

- [ ] **Step 6: Run existing editor tests to verify they still pass**

Run: `pytest tests/test_editor.py -v`
Expected: PASS (all existing tests, including `from editor import UndoStack, mosaic`)

- [ ] **Step 7: Run all tests**

Run: `pytest -x -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add drawing.py tests/test_drawing.py editor.py
git commit -m "refactor: extract shared drawing functions to drawing.py"
```

---

### Task 2: AnnotationWindow 核心（窗口 + 背景 + 暗化遮罩 + 选区边框）

**Files:**
- Create: `annotation.py`
- Create: `tests/test_annotation.py`

**Interfaces:**
- Consumes: `drawing.py` 的函数（Task 1）
- Produces:
  - `AnnotationWindow` 类：
    - `__init__(self, full_img, region, on_confirm, on_copy, on_save, on_cancel, root=None)` — 创建全屏不透明标注窗口
    - `full_img: PIL.Image` — 全屏截图
    - `region: tuple` — (left, top, width, height)，虚拟桌面坐标
    - `region_img: PIL.Image` — 选区 crop（标注画在这上面）
    - `original_region: PIL.Image` — 选区 crop 的原始副本
    - `tool: str` — 当前工具（"brush"/"rect"/"ellipse"/"arrow"/"mosaic"/"text"）
    - `color: str` — 标注颜色
    - `width: int` — 标注粗细
    - `undo: UndoStack` — 撤销栈
    - `show()` — 运行窗口（非阻塞，root 传入时）
    - `_on_confirm()` — 调用 on_confirm(region_img)，关闭窗口
    - `_on_copy()` — 调用 on_copy(region_img)，关闭窗口
    - `_on_save()` — 调用 on_save(region_img)，关闭窗口
    - `_on_cancel()` — 调用 on_cancel()，关闭窗口
    - `_render_region()` — 更新选区 PhotoImage
    - `_render_masks()` — 重建 4 块暗化遮罩
    - `_on_press(event)` / `_on_drag(event)` / `_on_release(event)` — 鼠标事件
    - `_tb_show(pos, size)` — 显示工具条
    - `_tb_hide()` — 隐藏工具条

- [ ] **Step 1: Write failing tests for AnnotationWindow core**

```python
# tests/test_annotation.py
import tkinter as tk
from unittest.mock import MagicMock, call
import pytest
from PIL import Image
from annotation import AnnotationWindow


@pytest.fixture
def fake_root():
    """让 AnnotationWindow 使用 mock 的 Tk 窗口，不创建真实 GUI。"""
    import sys
    import types

    with pytest.MonkeyPatch.context() as m:
        mock_root = MagicMock(name="tk_root")
        mock_root.geometry = MagicMock()
        mock_root.winfo_id = MagicMock(return_value=12345)
        m.setattr(tk, "Tk", lambda: mock_root)
        m.setattr(tk, "Toplevel", MagicMock(return_value=mock_root))
        m.setattr(tk, "Canvas", MagicMock(return_value=MagicMock(name="canvas")))
        m.setattr(tk, "Frame", MagicMock(return_value=MagicMock(name="frame")))
        m.setattr(tk, "Label", MagicMock(return_value=MagicMock(name="label")))
        # mock ImageTk
        fake_pil = types.ModuleType("PIL")
        fake_pil_image = types.ModuleType("PIL.Image")
        fake_pil_imagetk = types.ModuleType("PIL.ImageTk")
        fake_pil_imagetk.PhotoImage = MagicMock(name="PhotoImage")
        fake_pil.Image = fake_pil_image
        fake_pil.ImageTk = fake_pil_imagetk
        m.setitem(sys.modules, "PIL", fake_pil)
        m.setitem(sys.modules, "PIL.Image", fake_pil_image)
        m.setitem(sys.modules, "PIL.ImageTk", fake_pil_imagetk)
        yield mock_root


def test_annotation_window_init_crops_region(fake_root):
    full = Image.new("RGB", (100, 100), (255, 255, 255))
    # 画一个红块在 (20, 30, 60, 70)
    from PIL import ImageDraw
    d = ImageDraw.Draw(full)
    d.rectangle([20, 30, 60, 70], fill=(255, 0, 0))

    win = AnnotationWindow(
        full_img=full,
        region=(20, 30, 40, 40),
        on_confirm=lambda img: None,
        on_copy=lambda img: None,
        on_save=lambda img: None,
        on_cancel=lambda: None,
        root=MagicMock(),
    )
    # region_img 应该是 full 的 (20, 30, 60, 70) crop
    assert win.region_img.size == (40, 40)
    # crop 中心应为红色
    assert win.region_img.getpixel((20, 20)) == (255, 0, 0)


def test_annotation_window_confirm_passes_region_img(fake_root):
    full = Image.new("RGB", (100, 100), (255, 255, 255))
    confirmed = {}

    win = AnnotationWindow(
        full_img=full,
        region=(10, 10, 50, 50),
        on_confirm=lambda img: confirmed.update({"img": img}),
        on_copy=lambda img: None,
        on_save=lambda img: None,
        on_cancel=lambda: None,
        root=MagicMock(),
    )
    win._on_confirm()
    assert "img" in confirmed
    assert confirmed["img"].size == (50, 50)


def test_annotation_window_copy_passes_region_img(fake_root):
    full = Image.new("RGB", (100, 100), (0, 0, 0))
    copied = {}

    win = AnnotationWindow(
        full_img=full,
        region=(10, 10, 30, 30),
        on_confirm=lambda img: None,
        on_copy=lambda img: copied.update({"img": img}),
        on_save=lambda img: None,
        on_cancel=lambda: None,
        root=MagicMock(),
    )
    win._on_copy()
    assert "img" in copied
    assert copied["img"].size == (30, 30)


def test_annotation_window_cancel_calls_on_cancel(fake_root):
    full = Image.new("RGB", (100, 100), (0, 0, 0))
    cancelled = {}

    win = AnnotationWindow(
        full_img=full,
        region=(10, 10, 30, 30),
        on_confirm=lambda img: None,
        on_copy=lambda img: None,
        on_save=lambda img: None,
        on_cancel=lambda: cancelled.update({"called": True}),
        root=MagicMock(),
    )
    win._on_cancel()
    assert cancelled.get("called") is True


def test_adjust_region_clears_annotations(fake_root):
    full = Image.new("RGB", (100, 100), (255, 255, 255))
    from PIL import ImageDraw
    d = ImageDraw.Draw(full)
    d.rectangle([20, 20, 60, 60], fill=(255, 0, 0))

    win = AnnotationWindow(
        full_img=full,
        region=(20, 20, 40, 40),
        on_confirm=lambda img: None,
        on_copy=lambda img: None,
        on_save=lambda img: None,
        on_cancel=lambda: None,
        root=MagicMock(),
    )
    # 在 region_img 上画一条线（模拟标注）
    d2 = ImageDraw.Draw(win.region_img)
    d2.line([(0, 0), (40, 40)], fill=(0, 0, 255), width=3)

    # 调整选区（模拟手柄拖动）
    win._adjust_region(10, 10, 70, 70)

    # region_img 应该被重新 crop（从 full_img 的 10,10 到 70,70）
    assert win.region_img.size == (60, 60)
    # 之前的标注应该消失（重新 crop 后是原始 full 的内容）
    # 中心 (30, 30) 在 full 中是红色区域，所以 crop 后中心也是红色
    assert win.region_img.getpixel((30, 30)) == (255, 0, 0)
    # 撤销栈应该被清空
    assert win.undo.pop() is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_annotation.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'annotation'"

- [ ] **Step 3: Create annotation.py**

Create `annotation.py` with the `AnnotationWindow` class. Key structure:

```python
import sys
import tkinter as tk
from PIL import Image, ImageDraw, ImageTk
from drawing import (
    draw_arrow, draw_rect, draw_ellipse, draw_brush, draw_text, mosaic, UndoStack,
)

# 主题色（与 editor.py / selector.py 一致）
TB_BG = "#2b2f36"
TB_FG = "#f5f6f8"
TB_HOVER = "#4a90e2"
TB_HOVER_FG = "#ffffff"
TB_SEP = "#4a5160"
TB_FONT = ("Microsoft YaHei UI", 9)
LABEL_BG = "#2b2f36"
LABEL_FG = "#f5f6f8"

# 暗化遮罩 alpha（0-255，128 ≈ 50%）
MASK_ALPHA = 128


class AnnotationWindow:
    TOOLS = ("brush", "rect", "ellipse", "arrow", "mosaic", "text")
    TOOL_ICONS = {"brush": "✏", "rect": "▭", "ellipse": "◯", "arrow": "↗",
                  "mosaic": "▦", "text": "T"}
    TOOL_LABELS = {"brush": "画笔", "rect": "矩形", "ellipse": "圆形",
                   "arrow": "箭头", "mosaic": "打码", "text": "文字"}

    MIN_SIZE = 5
    HANDLE_SIZE = 12
    MARGIN = 8
    HANDLES = ("nw", "n", "ne", "e", "se", "s", "sw", "w")
    _CURSORS = {"nw": "fleur", "n": "sb_up_arrow", "ne": "crosshair",
                "e": "sb_right_arrow", "se": "fleur", "s": "sb_down_arrow",
                "sw": "crosshair", "w": "sb_left_arrow", "move": "fleur"}

    def __init__(self, full_img, region, on_confirm, on_copy, on_save, on_cancel, root=None):
        self.full_img = full_img
        self.region = region  # (left, top, w, h)
        self.on_confirm = on_confirm
        self.on_copy = on_copy
        self.on_save = on_save
        self.on_cancel = on_cancel
        self._owns_root = root is None
        self._shared_root = root

        # 选区 crop
        l, t, w, h = region
        self.region_img = full_img.crop((l, t, l + w, t + h))
        self.original_region = self.region_img.copy()

        # 标注状态
        self.tool = "brush"
        self.color = "#ff0000"
        self.width = 3
        self.undo = UndoStack(maxlen=10)
        self.drawing = False
        self.draw_start = None
        self._brush_last = None
        self._preview_id = None
        self._tool_btns = {}

        # 选区调整状态
        self._mode = "select"  # "select" 或 "adjust"
        self._region_rect = None  # (l, t, r, b) canvas 坐标
        self._drag_op = None
        self._drag_start = None
        self._drag_region = None
        self._items = {}
        self._tb_visible = False
        self._tb_size = None

        # 窗口
        self._build_ui()

    def _build_ui(self):
        # 创建全屏不透明窗口（与 selector 相同的 Win32 强制扩展逻辑）
        # Canvas 铺满，背景 = full_img
        # 4 块暗化遮罩
        # 选区边框 + 8 个手柄
        # 工具条
        ...

    def show(self):
        if self._owns_root:
            self.root.mainloop()
        # 共享根窗口：非阻塞

    # --- 渲染 ---
    def _render_region(self): ...
    def _render_masks(self): ...
    def _render_all(self): ...

    # --- 选区调整 ---
    def _adjust_region(self, l, t, r, b): ...
    def _handle_coords(self, l, t, r, b): ...
    def _hit_test(self, x, y): ...

    # --- 标注 ---
    def _on_press(self, event): ...
    def _on_drag(self, event): ...
    def _on_release(self, event): ...
    def _draw_preview(self, x0, y0, x1, y1): ...
    def _input_text(self, x, y): ...
    def _undo(self): ...

    # --- 结果 ---
    def _on_confirm(self): ...
    def _on_copy(self): ...
    def _on_save(self): ...
    def _on_cancel(self): ...

    # --- 工具条 ---
    def _build_toolbar(self): ...
    def _tb_show(self, pos, size): ...
    def _tb_hide(self): ...
```

Key implementation notes:
- `_build_ui`: Use the same Win32 fullscreen logic as selector.py's `_force_fullscreen`. Canvas bg = full_img via PhotoImage. 4 dimming masks via PIL (create black RGBA image with alpha=128, crop to each corner region, convert to PhotoImage, create_image on canvas).
- `_render_masks`: Create 4 PIL images (black, alpha=128), size = each corner region, convert to PhotoImage, display on canvas at the 4 corners. Only rebuild when region changes, not when annotation changes.
- `_render_region`: Convert region_img to PhotoImage, display at (l, t) on canvas. Only update when annotation changes.
- `_adjust_region(l, t, r, b)`: Re-crop region_img from full_img, clear undo stack, reset region_img to original crop, rebuild masks. This is called when the user moves/resizes the selection via handles.
- `_on_confirm/copy/save/cancel`: Call the callback with region_img, then close the window (withdraw + cleanup).
- `_on_press/_on_drag/_on_release`: First check if the click is on a handle or inside the region (adjust mode). If inside the region and a tool is selected, start annotation (drawing mode). If on a handle, start region adjustment.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_annotation.py -v`
Expected: PASS

- [ ] **Step 5: Run all tests**

Run: `pytest -x -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add annotation.py tests/test_annotation.py
git commit -m "feat: AnnotationWindow core (fullscreen, dimming masks, region adjust)"
```

---

### Task 3: AnnotationWindow 标注功能（工具条 + 绘制 + 撤销）

**Files:**
- Modify: `annotation.py`（添加标注工具条 + 绘制逻辑 + 撤销）
- Modify: `tests/test_annotation.py`（添加标注功能测试）

**Interfaces:**
- Consumes: `drawing.py` 的函数（Task 1）、AnnotationWindow 核心（Task 2）
- Produces: AnnotationWindow 的标注功能完整可用

- [ ] **Step 1: Write failing tests for annotation tools**

```python
# 追加到 tests/test_annotation.py

def test_brush_annotation_draws_on_region_img(fake_root):
    full = Image.new("RGB", (100, 100), (255, 255, 255))
    win = AnnotationWindow(
        full_img=full,
        region=(10, 10, 80, 80),
        on_confirm=lambda img: None,
        on_copy=lambda img: None,
        on_save=lambda img: None,
        on_cancel=lambda: None,
        root=MagicMock(),
    )
    win.tool = "brush"
    win.color = "#000000"
    win.width = 3

    # 模拟按下 (20, 20) canvas 坐标 → region_img 坐标 (10, 10)
    class E:
        x, y = 20, 20
    win._on_press(E())
    # 模拟拖动到 (60, 60) canvas → region_img (50, 50)
    class E2:
        x, y = 60, 60
    win._on_drag(E2())
    win._on_release(E2())

    # region_img 上应有黑色像素（画笔线条经过）
    assert win.region_img.getpixel((30, 30)) == (0, 0, 0)


def test_rect_annotation_draws_on_region_img(fake_root):
    full = Image.new("RGB", (100, 100), (255, 255, 255))
    win = AnnotationWindow(
        full_img=full,
        region=(10, 10, 80, 80),
        on_confirm=lambda img: None,
        on_copy=lambda img: None,
        on_save=lambda img: None,
        on_cancel=lambda: None,
        root=MagicMock(),
    )
    win.tool = "rect"
    win.color = "#ff0000"
    win.width = 2

    class E:
        x, y = 20, 20
    win._on_press(E())
    class E2:
        x, y = 80, 80
    win._on_release(E2())

    # region_img 上应有红色矩形边框
    # 中心 (40, 40) 在 region_img 坐标 (30, 30)，矩形边框经过
    assert win.region_img.getpixel((10, 40)) == (255, 0, 0)


def test_undo_reverts_annotation(fake_root):
    full = Image.new("RGB", (100, 100), (255, 255, 255))
    win = AnnotationWindow(
        full_img=full,
        region=(10, 10, 80, 80),
        on_confirm=lambda img: None,
        on_copy=lambda img: None,
        on_save=lambda img: None,
        on_cancel=lambda: None,
        root=MagicMock(),
    )
    win.tool = "rect"
    win.color = "#ff0000"
    win.width = 2

    class E:
        x, y = 20, 20
    win._on_press(E())
    class E2:
        x, y = 80, 80
    win._on_release(E2())

    # 撤销
    win._undo()
    # region_img 应该恢复为原始 crop（白色）
    assert win.region_img.getpixel((40, 40)) == (255, 255, 255)


def test_mosaic_annotation(fake_root):
    full = Image.new("RGB", (100, 100), (255, 255, 255))
    from PIL import ImageDraw
    d = ImageDraw.Draw(full)
    d.line([(10, 10), (90, 90)], fill=(0, 0, 0), width=5)

    win = AnnotationWindow(
        full_img=full,
        region=(0, 0, 100, 100),
        on_confirm=lambda img: None,
        on_copy=lambda img: None,
        on_save=lambda img: None,
        on_cancel=lambda: None,
        root=MagicMock(),
    )
    win.tool = "mosaic"
    win.width = 8

    class E:
        x, y = 10, 10
    win._on_press(E())
    class E2:
        x, y = 90, 90
    win._on_release(E2())

    # 马赛克后，对角线区域应有模糊
    has_mid = any(win.region_img.getpixel((x, y)) not in ((0, 0, 0), (255, 255, 255))
                  for x in range(0, 100, 5) for y in range(0, 100, 5))
    assert has_mid


def test_adjust_region_after_annotation_clears(fake_root):
    full = Image.new("RGB", (100, 100), (255, 255, 255))
    win = AnnotationWindow(
        full_img=full,
        region=(10, 10, 80, 80),
        on_confirm=lambda img: None,
        on_copy=lambda img: None,
        on_save=lambda img: None,
        on_cancel=lambda: None,
        root=MagicMock(),
    )
    win.tool = "rect"
    win.color = "#ff0000"
    win.width = 2

    class E:
        x, y = 20, 20
    win._on_press(E())
    class E2:
        x, y = 80, 80
    win._on_release(E2())

    # 调整选区
    win._adjust_region(5, 5, 95, 95)

    # region_img 应该被重新 crop，之前的标注消失
    assert win.region_img.size == (90, 90)
    assert win.region_img.getpixel((45, 45)) == (255, 255, 255)
    # 撤销栈被清空
    assert win.undo.pop() is None


def test_text_annotation(fake_root):
    """文字工具：_input_text 内部弹输入框，这里直接测 _input_text 的绘制逻辑。"""
    full = Image.new("RGB", (100, 100), (255, 255, 255))
    win = AnnotationWindow(
        full_img=full,
        region=(10, 10, 80, 80),
        on_confirm=lambda img: None,
        on_copy=lambda img: None,
        on_save=lambda img: None,
        on_cancel=lambda: None,
        root=MagicMock(),
    )
    win.tool = "text"
    win.color = "#000000"

    # 直接调用 _input_text 的绘制部分（跳过弹窗）
    # _input_text(x, y) 接收 region_img 坐标
    from unittest.mock import patch
    with patch.object(win, "_input_text_dialog") as mock_dialog:
        # 模拟用户输入 "Hi"
        mock_dialog.return_value = "Hi"
        win._input_text(20, 20)

    # 文字应该画在 region_img 上
    has_dark = any(win.region_img.getpixel((x, y)) != (255, 255, 255)
                   for x in range(15, 45) for y in range(15, 35))
    assert has_dark
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_annotation.py -v`
Expected: Some FAIL (annotation tools not yet implemented)

- [ ] **Step 3: Implement annotation tools in annotation.py**

In `AnnotationWindow`:

- `_build_toolbar`: Build a toolbar Toplevel with:
  - Tool buttons (brush/rect/ellipse/arrow/mosaic/text) — each is a tk.Label with icon, click selects tool
  - Color button — tk.Button, click opens colorchooser
  - Width spinbox — tk.Spinbox (1-30)
  - Undo button — "↩"
  - Confirm button — "✓ 确认"
  - Copy button — "⧉ 复制"
  - Save button — "💾 保存"
  - Cancel button — "✕ 取消"
  - Toolbar positioned below the region (reuse _tb_show logic from selector.py)

- `_select_tool(name)`: Set self.tool, refresh button styles

- `_on_press(event)`:
  1. Check if click is on a handle → start region adjustment
  2. Check if click is inside the region:
     - If tool is "text" → call _input_text(region_x, region_y)
     - Else → start drawing (self.drawing = True, self.draw_start = (region_x, region_y), push undo)
  3. If click is outside the region → do nothing (or start region adjustment if in select mode)

- `_on_drag(event)`:
  - If region adjustment → update region
  - If drawing:
    - brush: draw line from _brush_last to current pos on region_img, update PhotoImage
    - rect/ellipse/arrow: update preview on canvas

- `_on_release(event)`:
  - If region adjustment → end adjustment
  - If drawing:
    - brush: finalize
    - rect: draw_rect on region_img, update PhotoImage
    - ellipse: draw_ellipse on region_img
    - arrow: draw_arrow on region_img
    - mosaic: mosaic on region_img
    - Clear preview

- `_draw_preview(x0, y0, x1, y1)`: Draw temporary preview shape on canvas (in canvas coords, not region_img coords)

- `_input_text(x, y)`: 调用 `_input_text_dialog(x, y)` 弹输入框（Toplevel + Entry），获取文本后 draw_text on region_img，update PhotoImage。`_input_text_dialog` 是独立方法，方便测试 mock。

- `_undo()`: Pop from undo stack, restore region_img, update PhotoImage

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_annotation.py -v`
Expected: PASS

- [ ] **Step 5: Run all tests**

Run: `pytest -x -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add annotation.py tests/test_annotation.py
git commit -m "feat: annotation tools (brush/rect/ellipse/arrow/mosaic/text + undo)"
```

---

### Task 4: app.py 接入 AnnotationWindow

**Files:**
- Modify: `app.py:60-71`（process_region_selection 改为打开 AnnotationWindow）
- Modify: `app.py:87-102`（do_region_capture 的 on_select 回调适配）

**Interfaces:**
- Consumes: `AnnotationWindow`（Task 2+3）、`capture.py` 的函数
- Produces: 完整流程可用——Ctrl+Shift+X → 框选 → 标注 → 确认/复制/保存/取消

- [ ] **Step 1: Write a test for the integration**

```python
# tests/test_app_integration.py
"""验证 process_region_selection 打开 AnnotationWindow 而不是直接打开 Editor。"""
import pytest
from unittest.mock import MagicMock, patch
from PIL import Image


@pytest.fixture
def mock_annotation():
    with patch("app.AnnotationWindow") as mock_aw:
        mock_aw.return_value = MagicMock()
        yield mock_aw


def test_process_region_selection_opens_annotation(mock_annotation):
    import app
    app._tk_root_holder["root"] = MagicMock()

    full = Image.new("RGB", (100, 100), (255, 255, 255))
    region = (10, 10, 50, 50)

    app.process_region_selection(full, region)

    # AnnotationWindow 应该被调用
    mock_annotation.assert_called_once()
    # 检查参数
    call_args = mock_annotation.call_args
    assert call_args[0][0] is full  # full_img
    assert call_args[0][1] == region  # region


def test_process_region_selection_none_region_does_nothing(mock_annotation):
    import app
    app._tk_root_holder["root"] = MagicMock()

    full = Image.new("RGB", (100, 100), (255, 255, 255))
    app.process_region_selection(full, None)

    # AnnotationWindow 不应该被调用
    mock_annotation.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_app_integration.py -v`
Expected: FAIL (process_region_selection currently opens Editor, not AnnotationWindow)

- [ ] **Step 3: Modify app.py**

In `app.py`:
- Add import: `from annotation import AnnotationWindow`
- Change `process_region_selection` to:

```python
def process_region_selection(full, region):
    """区域选区结果 → 打开标注窗口。region 为 None 时不做任何事。

    full: 全屏截图（PIL Image）
    region: (left, top, width, height) 虚拟桌面坐标
    必须在主线程（Tk 主循环所在线程）调用。
    """
    if region is None:
        return
    root = _tk_root_holder.get("root")

    def on_confirm(annotated_img):
        # 标注结果交给 Editor（保留精修/OCR/保存）
        Editor(annotated_img, on_close=None, root=root).show()

    def on_copy(annotated_img):
        _safe_copy_to_clipboard(annotated_img)

    def on_save(annotated_img):
        save_image(annotated_img, None)

    def on_cancel():
        pass

    AnnotationWindow(
        full_img=full,
        region=region,
        on_confirm=on_confirm,
        on_copy=on_copy,
        on_save=on_save,
        on_cancel=on_cancel,
        root=root,
    ).show()
```

- Remove the old `capture_region` call (no longer needed — AnnotationWindow crops from full)
- Remove the old `Editor(region_img, ...)` call

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_app_integration.py -v`
Expected: PASS

- [ ] **Step 5: Run all tests**

Run: `pytest -x -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_app_integration.py
git commit -m "feat: wire AnnotationWindow into app.py flow"
```

---

### Task 5: 手动验证 + 收尾

**Files:**
- 无新文件，手动验证全流程

**Interfaces:**
- Consumes: 所有前序任务

- [ ] **Step 1: 跑应用，验证完整流程**

1. 启动 `python app.py`
2. 按 Ctrl+Shift+X 触发框选
3. 拖选一个区域
4. 选区下方出现标注工具条（工具/颜色/粗细/撤销/确认/复制/保存/取消）
5. 选择画笔，在选区内画几条线
6. 选择矩形，画一个矩形
7. 选择箭头，画一个箭头
8. 选择文字，点击选区内位置，输入文字
9. 选择马赛克，拖选一个区域
10. 按 Ctrl+Z 撤销最后一步
11. 按 "✓ 确认" → Editor 窗口打开，显示标注后的选区
12. 在 Editor 中做精修（可选），保存

- [ ] **Step 2: 验证"复制"路径**

1. 重新框选
2. 画一个标注
3. 按 "⧉ 复制"
4. 打开 Paint/Word，Ctrl+V 粘贴，确认图片正确

- [ ] **Step 3: 验证"保存"路径**

1. 重新框选
2. 画一个标注
3. 按 "💾 保存"
4. 确认桌面生成了 PNG 文件

- [ ] **Step 4: 验证"取消"路径**

1. 重新框选
2. 画一个标注
3. 按 "✕ 取消"
4. 确认没有输出，窗口关闭

- [ ] **Step 5: 验证调整选区后标注作废**

1. 框选一个区域
2. 画一个标注
3. 拖动选区手柄（移动/缩放）
4. 确认标注消失，选区图恢复为原始截图

- [ ] **Step 6: 验证多显示器**

1. 在副屏框选一个区域
2. 画一个标注
3. 确认标注在正确位置

- [ ] **Step 7: 验证文字输入框 Esc 行为**

1. 框选一个区域
2. 选择文字工具
3. 点击选区内位置，弹输入框
4. 按 Esc 关闭输入框
5. 确认标注窗口仍在，可以继续标注

- [ ] **Step 8: 跑完整测试套件**

Run: `pytest -x -q`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "feat: complete inline annotation flow (Approach B)"
```
