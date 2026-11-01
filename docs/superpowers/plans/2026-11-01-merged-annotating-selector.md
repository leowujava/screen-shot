# 合并选区 + 标注（Merged Annotating Selector）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把截图的"框选区域"和"标注"两步合并成一步：按下的瞬间出现完整工具条，画完框直接标注，确认后进 Editor。

**Architecture:** 重写 `selector.py` 为 `Selector` 类，内部两个模式（select / adjust），合并原 `annotation.py` 的标注逻辑。`annotation.py` 删除。`app.py` 的 `process_region_selection` 改为直接处理 `Selector` 的回调结果（region + annotated_img），不再打开 `AnnotationWindow`。

**Tech Stack:** Python 3, tkinter, PIL, mss（截屏），pynput（快捷键），pytest（测试）。

**Spec:** `docs/superpowers/specs/2026-11-01-merged-annotating-selector-design.md`

## Global Constraints

- 仍使用同一个共享 Tk 根窗口（`_tk_root_holder["root"]`），所有 GUI 挂在其上（见 `app.py:384-392`）
- 仍用 mss 截屏坐标系（虚拟桌面），与 `capture.py` 一致
- 工具条仍是 `tk.Toplevel` 无边框置顶窗口（`wm_overrideredirect(True)`）
- 选区外暗化用 PIL 预渲染的半透明黑色遮罩（alpha=128，与现有 `annotation.py:36` 一致）
- 性能：工具条跟随选区时，每帧只走 `geometry()` 移动，不触发 Tcl 布局计算（`winfo_*` 只在首次显示时查询，之后缓存到 `_tb_size`，见 `selector.py:213-241` 与 `tests/test_selector.py:209-267` 的 `test_drag_frame_cost_bounded`）
- 颜色常量复用现有 `TB_BG`/`TB_FG`/`TB_HOVER`/`TB_HOVER_FG`/`TB_SEP`/`TB_FONT`（`selector.py:9-14`）
- 平台：Windows（`sys.platform == "win32"`），用 `ctypes` 调 Win32 API

## Review Focus

- **画框阶段工具误触**：select 模式（还没画框）时工具按钮应灰色禁用、不响应点击；adjust 模式才启用。→ Task 5 测试 `test_toolbar_disabled_in_select_mode` 与 `test_toolbar_enabled_in_adjust_mode`
- **工具条跟随性能**：100 帧拖动期间 `winfo_*` 总调用次数必须 ≤ 10（只在首次显示时查询）。→ Task 6 测试 `test_drag_frame_cost_bounded`（迁移自现有 test_selector.py，适配新类）
- **调整选区清空标注**：拖手柄/移动选区后，`region_img` 重新从 `full_img` crop，之前的标注消失，撤销栈清空。→ Task 4 测试 `test_adjust_region_clears_annotations`
- **4K 选区 snap**：拖到屏幕边缘时选区应 snap 到完整边缘（差 1px 选不满的回归）。→ Task 6 测试 `test_4k_full_screen_selection`
- **确认结果坐标映射**：画布坐标 → mss 虚拟桌面坐标的 scale 映射不能错（多屏时 x 可能为负）。→ Task 6 测试 `test_select_to_adjust_flow`

---

### Task 1: 重写 `selector.py` 基础骨架（合并类 + 模式 + 回调签名）

**Files:**
- Modify: `selector.py`（重写整个文件，类名 `RegionSelector` → `Selector`，合并 select + adjust + 标注基础）
- Test: `tests/test_selector.py`（保留现有测试，适配新类名；本任务只加"回调签名"测试）

**Interfaces:**
- Consumes: `drawing.py` 的 `draw_brush`/`draw_rect`/`draw_ellipse`/`draw_arrow`/`draw_text`/`mosaic`/`UndoStack`；`capture.py` 的 `capture_fullscreen`（由 `app.py` 调用，不在 selector 内）
- Produces:
  - 类 `Selector(x, y, width, height, full_img, on_select, root=None)`
  - `on_select` 回调签名改为 `(region, annotated_img)`：
    - 确认：`(region, annotated_img)`，`region = (left, top, width, height)` mss 虚拟桌面坐标，`annotated_img` 是 PIL Image（选区 crop + 标注）
    - 取消：`(None, None)`
  - 属性 `self.tool`、`self.color`、`self.width`、`self.undo`（标注状态，与 `annotation.py:70-73` 一致）
  - 属性 `self._mode`（`"select"` / `"adjust"`）、`self._region`（canvas 坐标 `(l, t, r, b)`）
  - 方法 `_select_tool(name)`、`_pick_color()`、`_on_width_change()`、`_undo()`（标注，与 `annotation.py` 一致）
  - 方法 `_tb_show(pos, size)`、`_tb_hide()`（工具条定位，复用现有 `selector.py:213-251`）
  - 方法 `_render_all()`、`_render_masks(l,t,r,b)`、`_render_region(l,t)`、`_render_handles(l,t,r,b)`（渲染，合并现有 selector 与 annotation 的逻辑）

本任务只做骨架：类构造、模式切换、回调签名、空的方法占位。不实现标注绘制、不实现工具条按钮的启用/禁用状态。

- [ ] **Step 1: 写失败测试（回调签名）**

在 `tests/test_selector.py` 末尾加：

```python
def test_selector_confirm_passes_region_and_img(fake_root):
    """确认时 on_select 收到 (region, annotated_img)。"""
    from PIL import Image
    full = Image.new("RGB", (200, 200), (255, 255, 255))
    results = {}
    sel = Selector(
        x=0, y=0, width=200, height=200, full_img=full,
        on_select=lambda region, img: results.update(region=region, img=img),
    )
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


def test_selector_cancel_passes_none(fake_root):
    """取消时 on_select 收到 (None, None)。"""
    results = {}
    sel = Selector(
        x=0, y=0, width=200, height=200,
        full_img=None,
        on_select=lambda region, img: results.update(region=region, img=img),
    )
    sel._cancel()
    assert results["region"] is None
    assert results["img"] is None
```

注意：`full_img` 参数是新增的（原 `RegionSelector` 没有）。测试里用 `Image.new` 构造，不需要真实截屏。

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_selector.py::test_selector_confirm_passes_region_and_img tests/test_selector.py::test_selector_cancel_passes_none -v`
Expected: FAIL（`Selector` 未定义，或 `full_img` 参数不存在）

- [ ] **Step 3: 重写 `selector.py` 骨架**

把 `selector.py` 整个文件重写。类名改为 `Selector`，构造函数签名：

```python
def __init__(self, x, y, width, height, full_img, on_select, root=None):
```

关键改动点（相对原 `RegionSelector`）：
- 新增 `self.full_img = full_img`（PIL Image，全屏截图，由 `app.py` 传入）
- `self.on_select` 回调签名改为接收 `(region, annotated_img)`
- 新增标注状态属性（从 `annotation.py:70-73` 搬过来）：
  ```python
  self.tool = "brush"
  self.color = "#ff0000"
  self.width = 3
  self.undo = UndoStack(maxlen=10)
  self.drawing = False
  self.draw_start = None
  self._brush_last = None
  self._preview_id = None
  self._tool_btns = {}
  self._tb_btns = {}
  self._region_img = None   # 选区 crop（标注画在这上面）
  ```
- 新增 `self._init_region_img()` 方法：从 `self.full_img` crop 出 `self._region_img`，按 `self._region` 的 `(l, t, r, b)`：
  ```python
  def _init_region_img(self):
      l, t, r, b = self._region
      self._region_img = self.full_img.crop((l, t, r, b))
  ```
- `_confirm()` 改为：把 `self._region` 映射到 mss 虚拟桌面坐标得到 `region`，把 `self._region_img` 作为 `annotated_img`，调 `self.on_select(region, annotated_img)`：
  ```python
  def _confirm(self):
      if self._region is None:
          self._cancel()
          return
      l, t, r, b = self._region
      vx_l = int(l * self._scale_x)
      vx_t = int(t * self._scale_y)
      vx_r = int(r * self._scale_x)
      vx_b = int(b * self._scale_y)
      region = (vx_l + self.x, vx_t + self.y, vx_r - vx_l, vx_b - vx_t)
      self._finish(region, self._region_img)

  def _cancel(self):
      self._finish(None, None)

  def _finish(self, region, annotated_img):
      if self._finished:
          return
      self._finished = True
      self.result = (region, annotated_img)
      self._tb_hide()
      self._hide_crosshair()
      self.root.after(0, self._exit)
  ```
- `_exit()` 末尾的回调改为 `self.on_select(region, annotated_img)`（解包 `self.result`）：
  ```python
  def _exit(self):
      region, annotated_img = self.result
      # ... 销毁/withdraw 窗口、清理画布（与现有 _exit 一致）...
      self.on_select(region, annotated_img)
  ```
- 工具条 `_build_toolbar` 先保持现有"确认/取消"小条（Task 2 再扩展为完整工具条），但按钮命令要适配新的 `_confirm()`/`_cancel()`（它们内部调 `_finish`，已经改好了，所以按钮命令不变）
- 其他方法（`_get_screen_size`、`_force_fullscreen`、`_tb_show`、`_tb_hide`、`_show_crosshair`、`_hide_crosshair`、`_handle_coords`、`_to_virtual`、`_calc_region`、`_normalize`、`_hit_test`、`_on_press`、`_on_drag`、`_on_release`、`show`）从原 `RegionSelector` 搬过来，逻辑不变

本任务**不**实现：标注绘制（`_on_press` 里进入选区内开始标注的逻辑）、工具条完整按钮、工具启用/禁用状态。这些在 Task 2-5。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_selector.py::test_selector_confirm_passes_region_and_img tests/test_selector.py::test_selector_cancel_passes_none -v`
Expected: PASS

- [ ] **Step 5: 跑现有 selector 测试（适配新类名）**

现有 `tests/test_selector.py` 里所有 `from selector import RegionSelector` 和 `RegionSelector(...)` 调用都要改成 `Selector`，并加 `full_img` 参数。

Run: `pytest tests/test_selector.py -v`
Expected: 全部 PASS（除了本任务新增的两个测试，其余现有测试适配后应通过）

注意：现有测试里 `RegionSelector(x=..., y=..., width=..., height=..., on_select=...)` 都要改成 `Selector(x=..., y=..., width=..., height=..., full_img=<PIL Image 或 None>, on_select=...)`。`full_img` 在纯坐标计算测试（`test_virtual_coord_conversion` 等）里可以传 `None`，因为这些测试不调用 `_init_region_img`。

- [ ] **Step 6: Commit**

```bash
git add selector.py tests/test_selector.py
git commit -m "refactor: rename RegionSelector to Selector, add full_img + new on_select signature"
```

---

### Task 2: 完整工具条（按钮 + 颜色 + 粗细 + 撤销 + 确认/复制/保存/取消）

**Files:**
- Modify: `selector.py`（扩展 `_build_toolbar`，从"确认/取消"小条扩展为完整工具条）
- Test: `tests/test_selector.py`（加工具条按钮测试）

**Interfaces:**
- Consumes: Task 1 的 `Selector` 类、`self._tool_btns`、`self._tb_btns`、`self.tool`、`self.color`、`self.width`
- Produces:
  - 工具条按钮集合（与 `annotation.py:546-594` 一致）：
    - 工具按钮：`brush`/`rect`/`ellipse`/`arrow`/`mosaic`/`text`（图标见 `annotation.py:41-42`）
    - 颜色按钮（`_pick_color`）
    - 粗细 Spinbox（`_on_width_change`）
    - 撤销按钮（`_undo`）
    - 操作按钮：`confirm`/`copy`/`save`/`cancel`
  - 方法 `_select_tool(name)`、`_pick_color()`、`_on_width_change()`、`_undo()`（从 `annotation.py:609-630` 搬过来）
  - 回调 `on_copy`/`on_save`（新增，与 `on_confirm` 同签名 `(region, annotated_img)`）

`Selector` 构造函数签名扩展为：

```python
def __init__(self, x, y, width, height, full_img, on_select, on_copy, on_save, root=None):
```

`on_select` 仍用于"确认"和"取消"，`on_copy`/`on_save` 是新的两个回调。`_finish` 改为接收一个 `callback` 参数：

```python
def _finish(self, callback):
    if self._finished:
        return
    self._finished = True
    self._tb_hide()
    self._hide_crosshair()
    self.root.after(0, self._exit)
    # 保存 callback，_exit 时调用
    self._pending_callback = callback

def _confirm(self):
    if self._region is None:
        self._cancel(); return
    l, t, r, b = self._region
    vx_l = int(l * self._scale_x); vx_t = int(t * self._scale_y)
    vx_r = int(r * self._scale_x); vx_b = int(b * self._scale_y)
    region = (vx_l + self.x, vx_t + self.y, vx_r - vx_l, vx_b - vx_t)
    self._finish(lambda: self.on_select(region, self._region_img))

def _copy(self):
    if self._region is None:
        self._cancel(); return
    l, t, r, b = self._region
    vx_l = int(l * self._scale_x); vx_t = int(t * self._scale_y)
    vx_r = int(r * self._scale_x); vx_b = int(b * self._scale_y)
    region = (vx_l + self.x, vx_t + self.y, vx_r - vx_l, vx_b - vx_t)
    self._finish(lambda: self.on_copy(region, self._region_img))

def _save(self):
    if self._region is None:
        self._cancel(); return
    l, t, r, b = self._region
    vx_l = int(l * self._scale_x); vx_t = int(t * self._scale_y)
    vx_r = int(r * self._scale_x); vx_b = int(b * self._scale_y)
    region = (vx_l + self.x, vx_t + self.y, vx_r - vx_l, vx_b - vx_t)
    self._finish(lambda: self.on_save(region, self._region_img))

def _cancel(self):
    self._finish(lambda: self.on_select(None, None))
```

`_exit` 改为：

```python
def _exit(self):
    # 销毁/withdraw 窗口、清理画布（与 Task 1 一致）
    cb = getattr(self, "_pending_callback", None)
    if cb is not None:
        cb()
```

- [ ] **Step 1: 写失败测试（工具条按钮存在 + 点击事件绑定）**

在 `tests/test_selector.py` 加：

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_selector.py::test_toolbar_has_all_buttons tests/test_selector.py::test_select_tool_updates_state -v`
Expected: FAIL（`_tool_btns`/`_tb_btns` 为空，或 `on_copy`/`on_save` 参数不存在）

- [ ] **Step 3: 扩展 `_build_toolbar`**

把 `selector.py` 的 `_build_toolbar` 从现有"确认/取消"小条扩展为完整工具条。结构完全照搬 `annotation.py:537-597`：

```python
def _build_toolbar(self):
    self.tb_win = tk.Toplevel(self.root)
    self.tb_win.wm_overrideredirect(True)
    self.tb_win.configure(bg=TB_BG, bd=1, relief="solid")
    self.tb_win.withdraw()

    bar = tk.Frame(self.tb_win, bg=TB_BG)
    bar.pack(fill="x", padx=4, pady=3)

    # 工具按钮
    for tool in self.TOOLS:
        b = tk.Label(bar, text=self.TOOL_ICONS[tool],
                     fg=TB_FG, bg=TB_BG, font=TB_FONT, padx=6, pady=2,
                     cursor="hand2")
        b.pack(side="left", padx=1)
        b.bind("<Enter>", lambda e, bb=b: bb.configure(bg=TB_HOVER, fg=TB_HOVER_FG))
        b.bind("<Leave>", lambda e, bb=b: bb.configure(bg=TB_BG, fg=TB_FG))
        b.bind("<Button-1>", lambda e, t=tool: self._select_tool(t))
        self._tool_btns[tool] = b

    # 分隔
    tk.Frame(bar, width=1, height=18, bg=TB_SEP).pack(side="left", padx=5)

    # 颜色
    color_btn = tk.Label(bar, text="🎨", fg=TB_FG, bg=TB_BG, font=TB_FONT,
                         padx=4, pady=2, cursor="hand2")
    color_btn.pack(side="left", padx=1)
    color_btn.bind("<Enter>", lambda e: color_btn.configure(bg=TB_HOVER, fg=TB_HOVER_FG))
    color_btn.bind("<Leave>", lambda e: color_btn.configure(bg=TB_BG, fg=TB_FG))
    color_btn.bind("<Button-1>", lambda e: self._pick_color())

    # 粗细
    self._width_var = tk.StringVar(master=self.root, value="3")
    spin = tk.Spinbox(self.tb_win, from_=1, to=30, width=3,
                      textvariable=self._width_var, font=TB_FONT,
                      bg=TB_BG, fg=TB_FG, bd=0, relief="flat")
    spin.pack(side="left", padx=2, in_=bar)
    spin.bind("<FocusOut>", lambda e: self._on_width_change())
    spin.bind("<Return>", lambda e: self._on_width_change())

    # 撤销
    undo_btn = tk.Label(bar, text="↩", fg=TB_FG, bg=TB_BG, font=TB_FONT,
                        padx=4, pady=2, cursor="hand2")
    undo_btn.pack(side="left", padx=2)
    undo_btn.bind("<Enter>", lambda e: undo_btn.configure(bg=TB_HOVER, fg=TB_HOVER_FG))
    undo_btn.bind("<Leave>", lambda e: undo_btn.configure(bg=TB_BG, fg=TB_FG))
    undo_btn.bind("<Button-1>", lambda e: self._undo())

    # 分隔
    tk.Frame(bar, width=1, height=18, bg=TB_SEP).pack(side="left", padx=5)

    # 确认/复制/保存/取消
    self._tb_btn(bar, "confirm", "✓ 确认", self._confirm)
    self._tb_btn(bar, "copy", "⧉ 复制", self._copy)
    self._tb_btn(bar, "save", "💾 保存", self._save)
    self._tb_btn(bar, "cancel", "✕ 取消", self._cancel)

    self.tb_win.bind("<Escape>", lambda e: self._cancel())
    self.tb_win.bind("<Return>", lambda e: self._confirm())
```

类属性（从 `annotation.py:40-44` 搬过来）：

```python
TOOLS = ("brush", "rect", "ellipse", "arrow", "mosaic", "text")
TOOL_ICONS = {"brush": "✏", "rect": "▭", "ellipse": "◯", "arrow": "↗",
              "mosaic": "▦", "text": "T"}
```

新增方法（从 `annotation.py:599-630` 搬过来）：`_tb_btn`、`_select_tool`、`_pick_color`、`_on_width_change`、`_undo`。

`_undo` 在 selector 里要同时调 `_render_all()` 重绘（annotation.py 里 `_undo` 调 `_render_all`，selector 里也是）：

```python
def _undo(self):
    prev = self.undo.pop()
    if prev is not None:
        self._region_img = prev
        self._render_all()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_selector.py::test_toolbar_has_all_buttons tests/test_selector.py::test_select_tool_updates_state -v`
Expected: PASS

- [ ] **Step 5: 跑全部 selector 测试**

Run: `pytest tests/test_selector.py -v`
Expected: 全部 PASS

- [ ] **Step 6: Commit**

```bash
git add selector.py tests/test_selector.py
git commit -m "feat: full toolbar with tools/color/width/undo/confirm-copy-save-cancel"
```

---

### Task 3: 标注绘制逻辑（brush/rect/ellipse/arrow/mosaic/text）

**Files:**
- Modify: `selector.py`（在 `_on_press`/`_on_drag`/`_on_release` 里加标注逻辑）
- Test: `tests/test_selector.py`（加标注测试，迁移自 `tests/test_annotation.py:146-318`）

**Interfaces:**
- Consumes: Task 1-2 的 `Selector`、`self._region_img`、`self.tool`、`self.color`、`self.width`、`self.drawing`、`self.draw_start`、`self._brush_last`、`self._preview_id`
- Produces:
  - `_on_press`：select 模式 → 创建最小框进 adjust + `_init_region_img()`；adjust 模式 → 手柄命中则 resize，选区内则开始标注
  - `_on_drag`：select→adjust 过渡 → 更新框大小 + 十字准星；adjust + 手柄 → resize + 重 crop；adjust + 标注 → 绘制
  - `_on_release`：清理拖动状态；标注完成时调用对应的 `draw_*` 函数
  - `_draw_preview(x0, y0, x1, y1)`：画布上画临时预览
  - `_input_text(x, y)` + `_input_text_dialog(x, y)`：文字工具（从 `annotation.py:491-521` 搬过来）

- [ ] **Step 1: 写失败测试（标注绘制，迁移自 test_annotation.py）**

从 `tests/test_annotation.py:146-318` 迁移以下测试到 `tests/test_selector.py`，把 `AnnotationWindow` 改成 `Selector`，参数加 `full_img`/`on_copy`/`on_save`：

```python
def test_brush_annotation_draws_on_region_img(fake_root):
    from PIL import Image
    full = Image.new("RGB", (100, 100), (255, 255, 255))
    sel = Selector(x=0, y=0, width=100, height=100, full_img=full,
                   on_select=lambda r, i: None,
                   on_copy=lambda r, i: None,
                   on_save=lambda r, i: None)
    sel._mode = "adjust"
    sel._region = (10, 10, 90, 90)
    sel._init_region_img()
    sel.tool = "brush"; sel.color = "#000000"; sel.width = 3

    class E:
        def __init__(self, x, y): self.x, self.y = x, y
    sel._on_press(E(20, 20))
    sel._on_drag(E(60, 60))
    sel._on_release(E(60, 60))
    # 画笔线条经过 (30, 30)
    assert sel._region_img.getpixel((20, 20)) == (0, 0, 0)


def test_rect_annotation_draws_on_region_img(fake_root):
    from PIL import Image
    full = Image.new("RGB", (100, 100), (255, 255, 255))
    sel = Selector(x=0, y=0, width=100, height=100, full_img=full,
                   on_select=lambda r, i: None,
                   on_copy=lambda r, i: None,
                   on_save=lambda r, i: None)
    sel._mode = "adjust"
    sel._region = (10, 10, 90, 90)
    sel._init_region_img()
    sel.tool = "rect"; sel.color = "#ff0000"; sel.width = 2

    class E:
        def __init__(self, x, y): self.x, self.y = x, y
    sel._on_press(E(20, 20))
    sel._on_release(E(80, 80))
    assert sel._region_img.getpixel((10, 40)) == (255, 0, 0)


def test_undo_reverts_annotation(fake_root):
    from PIL import Image
    full = Image.new("RGB", (100, 100), (255, 255, 255))
    sel = Selector(x=0, y=0, width=100, height=100, full_img=full,
                   on_select=lambda r, i: None,
                   on_copy=lambda r, i: None,
                   on_save=lambda r, i: None)
    sel._mode = "adjust"
    sel._region = (10, 10, 90, 90)
    sel._init_region_img()
    sel.tool = "rect"; sel.color = "#ff0000"; sel.width = 2

    class E:
        def __init__(self, x, y): self.x, self.y = x, y
    sel._on_press(E(20, 20))
    sel._on_release(E(80, 80))
    sel._undo()
    assert sel._region_img.getpixel((40, 40)) == (255, 255, 255)


def test_mosaic_annotation(fake_root):
    from PIL import Image, ImageDraw
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
    sel.tool = "mosaic"; sel.width = 8

    class E:
        def __init__(self, x, y): self.x, self.y = x, y
    sel._on_press(E(10, 10))
    sel._on_release(E(90, 90))
    has_mid = any(sel._region_img.getpixel((x, y)) not in ((0, 0, 0), (255, 255, 255))
                  for x in range(0, 100, 5) for y in range(0, 100, 5))
    assert has_mid


def test_text_annotation(fake_root):
    from PIL import Image
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
```

注意：测试里直接设 `sel._mode = "adjust"` 和 `sel._region = (...)` 然后调 `_init_region_img()`，跳过真实的 `_on_press` select→adjust 流程，这样测试更聚焦标注逻辑。

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_selector.py -k "annotation" -v`
Expected: FAIL（`_region_img` 未设置，或 `_on_press` 不处理标注）

- [ ] **Step 3: 实现标注逻辑**

在 `selector.py` 的 `_on_press`/`_on_drag`/`_on_release` 里加标注逻辑，逻辑照搬 `annotation.py:349-448`。

`_on_press`（合并 select→adjust + 手柄 + 标注）：

```python
def _on_press(self, event):
    if self._mode == "select":
        # 按下立刻创建最小框并进入 adjust 模式
        self.start = (event.x, event.y)
        self._mode = "adjust"
        x, y = event.x, event.y
        W, H = self._actual_w, self._actual_h
        x = max(0, min(x, W - 1))
        y = max(0, min(y, H - 1))
        self._region = (x, y, x + 1, y + 1)
        self._init_region_img()
        self._render_all()
        return
    if self._region is None:
        return
    hit = self._hit_test(event.x, event.y)
    if hit:
        self._drag_op = (hit, "resize")
        self._drag_start = (event.x, event.y)
        self._drag_region = self._region
        self.canvas.configure(cursor=self._CURSORS[hit])
        return
    # 选区内 → 开始标注
    l, t, r, b = self._region
    if l <= event.x <= r and t <= event.y <= b:
        rx, ry = event.x - l, event.y - t
        if self.tool == "text":
            self._input_text(rx, ry)
            self.drawing = False
            self.draw_start = None
            return
        self.drawing = True
        self.draw_start = (rx, ry)
        self._brush_last = (rx, ry)
        self.undo.push(self._region_img.copy())
        if self.tool == "brush":
            from PIL import ImageDraw
            d = ImageDraw.Draw(self._region_img)
            d.point(self.draw_start, fill=self.color)
            self._render_region(l, t)
        self.canvas.configure(cursor="crosshair")
```

`_on_drag`（合并 select→adjust 过渡 + 手柄 resize + 标注）：

```python
def _on_drag(self, event):
    # select→adjust 过渡：更新框大小
    if self._mode == "adjust" and self._drag_op is None and getattr(self, "start", None):
        sx, sy = self.start
        x1, y1 = min(sx, event.x), min(sy, event.y)
        x2, y2 = max(sx, event.x), max(sy, event.y)
        W, H = self._actual_w, self._actual_h
        if sx <= 1 or event.x <= 1: x1 = 0
        if sy <= 1 or event.y <= 1: y1 = 0
        if sx >= W - 2 or event.x >= W - 2: x2 = W
        if sy >= H - 2 or event.y >= H - 2: y2 = H
        w = x2 - x1; h = y2 - y1
        if w < self.MIN_SIZE and h < self.MIN_SIZE:
            return
        self._region = self._normalize((x1, y1, x2, y2))
        if self._region:
            l, t, r, b = self._region
            self._show_crosshair(event.x, event.y, r - l, b - t)
            self._init_region_img()
        self._render_all()
        return
    # 手柄 resize
    if self._mode == "adjust" and self._drag_op is not None and self._drag_start is not None:
        op, kind = self._drag_op
        dx = event.x - self._drag_start[0]
        dy = event.y - self._drag_start[1]
        l, t, r, b = self._drag_region
        if kind == "move":
            l, t, r, b = l + dx, t + dy, r + dx, b + dy
        else:
            if op in ("w", "sw", "nw"): l = l + dx
            if op in ("e", "ne", "se"): r = r + dx
            if op in ("n", "nw", "ne"): t = t + dy
            if op in ("s", "sw", "se"): b = b + dy
        self._region = self._normalize((l, t, r, b))
        self._hide_crosshair()
        self._adjust_region()
        return
    # 标注
    if self.drawing:
        l, t, r, b = self._region
        rx, ry = event.x - l, event.y - t
        rx = max(0, min(rx, r - l - 1))
        ry = max(0, min(ry, b - t - 1))
        if self.tool == "brush":
            draw_brush(self._region_img, [self._brush_last, (rx, ry)], self.color, self.width)
            self._brush_last = (rx, ry)
            self._render_region(l, t)
        else:
            sx, sy = self.draw_start
            self._draw_preview(sx + l, sy + t, rx + l, ry + t)
```

`_adjust_region`（调整选区后重 crop + 清空标注，从 `annotation.py:306-323` 搬过来，去掉参数，直接用 `self._region`）：

```python
def _adjust_region(self):
    """调整选区后重新 crop、清空标注和撤销栈。"""
    l, t, r, b = self._region
    W, H = self._actual_w, self._actual_h
    l = max(0, min(l, W))
    t = max(0, min(t, H))
    r = max(0, min(r, W))
    b = max(0, min(b, H))
    if r - l < self.MIN_SIZE or b - t < self.MIN_SIZE:
        return
    self._init_region_img()
    self.undo.clear()
    self._render_all()
```

`_on_release`（清理 + 标注完成）：

```python
def _on_release(self, event):
    self.start = None
    self._drag_op = None
    self._drag_start = None
    self._drag_region = None
    self.canvas.configure(cursor="crosshair")
    self._hide_crosshair()
    if self._region is None:
        self._mode = "select"
        self._render_all()
    if not self.drawing:
        return
    self.drawing = False
    l, t, r, b = self._region
    rx, ry = event.x - l, event.y - t
    rx = max(0, min(rx, r - l - 1))
    ry = max(0, min(ry, b - t - 1))
    sx, sy = self.draw_start
    self._clear_preview()
    if self.tool == "brush":
        pass  # 已在 drag 中完成
    elif self.tool == "rect":
        draw_rect(self._region_img,
                  [min(sx, rx), min(sy, ry), max(sx, rx), max(sy, ry)],
                  self.color, self.width)
    elif self.tool == "ellipse":
        if abs(rx - sx) < 1 and abs(ry - sy) < 1:
            return
        draw_ellipse(self._region_img,
                     [min(sx, rx), min(sy, ry), max(sx, rx), max(sy, ry)],
                     self.color, self.width)
    elif self.tool == "arrow":
        if abs(rx - sx) < 1 and abs(ry - sy) < 1:
            return
        draw_arrow(self._region_img, (sx, sy), (rx, ry), self.color, self.width)
    elif self.tool == "mosaic":
        self._region_img = mosaic(
            self._region_img,
            min(sx, rx), min(sy, ry), max(sx, rx), max(sy, ry),
            block=max(2, self.width // 2 or 4))
    self._render_region(l, t)
```

`_draw_preview`/`_clear_preview`/`_input_text`/`_input_text_dialog` 从 `annotation.py:450-521` 搬过来。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_selector.py -k "annotation" -v`
Expected: PASS

- [ ] **Step 5: 跑全部 selector 测试**

Run: `pytest tests/test_selector.py -v`
Expected: 全部 PASS

- [ ] **Step 6: Commit**

```bash
git add selector.py tests/test_selector.py
git commit -m "feat: annotation drawing (brush/rect/ellipse/arrow/mosaic/text) in selector"
```

---

### Task 4: 选区外暗化 + 选区明亮渲染（合并 selector 与 annotation 的渲染）

**Files:**
- Modify: `selector.py`（重写 `_render_all`，加 `_render_masks`/`_render_region`）
- Test: `tests/test_selector.py`（加渲染测试）

**Interfaces:**
- Consumes: Task 1-3 的 `Selector`、`self.full_img`、`self._region_img`、`self._region`、`self._items`
- Produces:
  - `_render_all()`：select 模式只画十字准星；adjust 模式画背景（full_img）+ 4 块遮罩 + 选区图 + 边框 + 手柄 + 工具条
  - `_render_masks(l, t, r, b)`：4 块半透明黑色遮罩（alpha=128），从 `annotation.py:228-263` 搬过来
  - `_render_region(l, t)`：选区 PhotoImage 贴到画布，从 `annotation.py:265-279` 搬过来
  - `_render_handles(l, t, r, b)`：8 个手柄，从 `annotation.py:281-300` 搬过来

- [ ] **Step 1: 写失败测试（adjust 模式渲染出遮罩 + 选区图）**

```python
def test_render_all_in_adjust_mode_creates_masks_and_region(fake_root):
    """adjust 模式下 _render_all 应创建 4 块遮罩 + 选区图。"""
    from PIL import Image
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
```

注意：测试里 `fake_root` fixture 已经 mock 了 `PIL.ImageTk.PhotoImage`，所以 `create_image` 不会真的渲染。`_items` 里记录的是 canvas item id（mock 的返回值）。

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_selector.py -k "render_all" -v`
Expected: FAIL（`_render_all` 不创建遮罩/选区图）

- [ ] **Step 3: 重写 `_render_all`**

```python
def _render_all(self):
    """渲染/更新全部。select 模式只画十字准星；adjust 模式画背景+遮罩+选区+边框+手柄+工具条。"""
    if self._mode != "adjust" or self._region is None:
        return
    l, t, r, b = self._region
    W, H = self._actual_w, self._actual_h

    # 背景（full_img 只贴一次）
    if "bg" not in self._items and self.full_img is not None:
        try:
            bg_photo = ImageTk.PhotoImage(self.full_img)
        except Exception:
            return
        self._bg_photo = bg_photo
        self._items["bg"] = self.canvas.create_image(0, 0, image=bg_photo, anchor="nw")

    # 4 块暗化遮罩
    self._render_masks(l, t, r, b)

    # 选区图
    self._render_region(l, t)

    # 选区边框
    if "rect" not in self._items:
        self._items["rect"] = self.canvas.create_rectangle(0, 0, 0, 0, outline="white", width=2)
    self.canvas.coords(self._items["rect"], l, t, r, b)

    # 手柄
    self._render_handles(l, t, r, b)

    # 工具条
    self._tb_show((l, t, r, b), (l, t, r - l, b - t))
```

`_render_masks`/`_render_region`/`_render_handles` 从 `annotation.py:228-300` 搬过来（注意 `annotation.py` 里 `_render_handles` 末尾有 `self.canvas.tag_raise(self._items["region"])`，保留）。

顶部加 import：`from PIL import Image, ImageTk`（`annotation.py:19` 已有，selector.py 需要加）。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_selector.py -k "render_all" -v`
Expected: PASS

- [ ] **Step 5: 跑全部 selector 测试**

Run: `pytest tests/test_selector.py -v`
Expected: 全部 PASS

- [ ] **Step 6: Commit**

```bash
git add selector.py tests/test_selector.py
git commit -m "feat: dimming masks + bright region rendering in adjust mode"
```

---

### Task 5: 工具条启用/禁用状态（select 禁用，adjust 启用）

**Files:**
- Modify: `selector.py`（加 `_set_toolbar_enabled(enabled)` 方法，在 `_on_press` select→adjust 时调用）
- Test: `tests/test_selector.py`（加启用/禁用测试）

**Interfaces:**
- Consumes: Task 2 的 `self._tool_btns`、`self._tb_btns`
- Produces:
  - 方法 `_set_toolbar_enabled(enabled: bool)`：启用时工具按钮正常色 + 响应点击；禁用时工具按钮灰色（`fg=TB_SEP`、`cursor="arrow"`）+ 不响应点击
  - select 模式：工具按钮禁用
  - adjust 模式：工具按钮启用

- [ ] **Step 1: 写失败测试**

```python
def test_toolbar_disabled_in_select_mode(fake_root):
    """select 模式下工具按钮应灰色禁用。"""
    sel = Selector(x=0, y=0, width=1920, height=1080, full_img=None,
                   on_select=lambda r, i: None,
                   on_copy=lambda r, i: None,
                   on_save=lambda r, i: None)
    assert sel._mode == "select"
    # 构造后应处于禁用状态
    for tool, b in sel._tool_btns.items():
        # 禁用时 fg 应为 TB_SEP（灰色）
        calls = [c for c in b.configure.call_args_list
                 if c.kwargs.get("fg") == TB_SEP]
        assert calls, f"工具 {tool} 在 select 模式未禁用（fg 不是灰色）"


def test_toolbar_enabled_in_adjust_mode(fake_root):
    """进入 adjust 模式后工具按钮应启用（fg 恢复 TB_FG）。"""
    sel = Selector(x=0, y=0, width=1920, height=1080, full_img=None,
                   on_select=lambda r, i: None,
                   on_copy=lambda r, i: None,
                   on_save=lambda r, i: None)
    # 模拟进入 adjust
    class E:
        def __init__(self, x, y): self.x, self.y = x, y
    sel._on_press(E(100, 100))
    assert sel._mode == "adjust"
    # 启用后至少有一次 configure(fg=TB_FG)
    for tool, b in sel._tool_btns.items():
        calls = [c for c in b.configure.call_args_list
                 if c.kwargs.get("fg") == TB_FG]
        assert calls, f"工具 {tool} 在 adjust 模式未启用（fg 不是正常色）"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_selector.py -k "toolbar_enabled or toolbar_disabled" -v`
Expected: FAIL（`_set_toolbar_enabled` 不存在）

- [ ] **Step 3: 实现 `_set_toolbar_enabled`**

```python
def _set_toolbar_enabled(self, enabled):
    """启用/禁用工具按钮。禁用时灰色 + 不响应。"""
    for tool, b in self._tool_btns.items():
        if enabled:
            b.configure(fg=TB_FG, bg=TB_BG, cursor="hand2")
            # 重新绑定点击事件（启用时）
            b.bind("<Button-1>", lambda e, t=tool: self._select_tool(t))
        else:
            b.configure(fg=TB_SEP, bg=TB_BG, cursor="arrow")
            # 解绑点击事件（禁用时）：绑定空函数
            b.bind("<Button-1>", lambda e: None)
```

在 `__init__` 末尾（`self._build_toolbar()` 之后）调 `self._set_toolbar_enabled(False)`（初始 select 模式禁用）。

在 `_on_press` 的 select→adjust 分支里，`self._mode = "adjust"` 之后调 `self._set_toolbar_enabled(True)`。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_selector.py -k "toolbar_enabled or toolbar_disabled" -v`
Expected: PASS

- [ ] **Step 5: 跑全部 selector 测试**

Run: `pytest tests/test_selector.py -v`
Expected: 全部 PASS

- [ ] **Step 6: Commit**

```bash
git add selector.py tests/test_selector.py
git commit -m "feat: toolbar enable/disable (disabled in select, enabled in adjust)"
```

---

### Task 6: 回归测试迁移（性能 + 4K snap + 坐标映射）

**Files:**
- Modify: `tests/test_selector.py`（确认 Task 1 已迁移的测试都通过，补全性能/4K/坐标测试）

**Interfaces:**
- Consumes: Task 1-5 的 `Selector`
- Produces: 无新接口，只确认现有回归测试通过

本任务不写新代码，只确认 Task 1 适配后的现有测试（`test_drag_frame_cost_bounded`、`test_4k_full_screen_selection`、`test_select_to_adjust_flow`、`test_virtual_coord_conversion`、`test_calc_region_normalized`、`test_calc_region_min_size`、`test_normalize`、`test_handle_coords`、`test_hit_test`）全部通过。如果有失败，修 selector.py 的实现（不是改测试）。

- [ ] **Step 1: 跑全部 selector 测试**

Run: `pytest tests/test_selector.py -v`
Expected: 全部 PASS

如果有失败，定位是 selector.py 实现问题还是测试适配问题。实现问题修 selector.py；测试适配问题（如 `full_img` 参数缺失）修测试。

- [ ] **Step 2: Commit（如有修复）**

```bash
git add selector.py tests/test_selector.py
git commit -m "fix: regression test fixes for merged selector"
```

---

### Task 7: 更新 `app.py`（删除 AnnotationWindow 调用，改为直接处理 Selector 结果）

**Files:**
- Modify: `app.py`（`process_region_selection` 改为接收 `(region, annotated_img)`，直接进 Editor；删除 `AnnotationWindow` import）
- Delete: `annotation.py`
- Delete: `tests/test_annotation.py`
- Modify: `tests/test_app_integration.py`（更新，验证 `process_region_selection` 直接进 Editor）

**Interfaces:**
- Consumes: Task 1 的 `Selector` 回调签名 `(region, annotated_img)`
- Produces:
  - `app.process_region_selection(region, annotated_img)`：新签名，直接进 Editor
  - `app.do_region_capture`：流程不变（工作线程截屏 → 主线程打开 Selector），但 `on_select` 回调改为新签名

- [ ] **Step 1: 写失败测试（process_region_selection 新签名）**

更新 `tests/test_app_integration.py`：

```python
"""验证 process_region_selection 直接打开 Editor（不再走 AnnotationWindow）。"""
import pytest
from unittest.mock import MagicMock, patch
from PIL import Image


@pytest.fixture
def mock_editor():
    with patch("app.Editor") as mock_ed:
        mock_ed.return_value = MagicMock()
        yield mock_ed


def test_process_region_selection_opens_editor(mock_editor):
    import app
    app._tk_root_holder["root"] = MagicMock()

    full = Image.new("RGB", (100, 100), (255, 255, 255))
    region = (10, 10, 50, 50)
    annotated = full.crop((10, 10, 60, 60))

    app.process_region_selection(region, annotated)

    mock_editor.assert_called_once()
    call_args = mock_editor.call_args
    assert call_args[0][0] is annotated  # image
    assert call_args[1].get("root") is app._tk_root_holder["root"]  # root kwarg


def test_process_region_selection_none_region_does_nothing(mock_editor):
    import app
    app._tk_root_holder["root"] = MagicMock()

    app.process_region_selection(None, None)

    mock_editor.assert_not_called()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_app_integration.py -v`
Expected: FAIL（`process_region_selection` 签名还是旧的 `(full, region)`）

- [ ] **Step 3: 改 `app.py`**

删除 `from annotation import AnnotationWindow`（`app.py:19`）。

`process_region_selection` 改为：

```python
def process_region_selection(region, annotated_img):
    """选区 + 标注结果 → 打开 Editor。region 为 None 时不做任何事。

    region: (left, top, width, height) 虚拟桌面坐标
    annotated_img: PIL Image（选区 crop + 标注）
    必须在主线程（Tk 主循环所在线程）调用。
    """
    if region is None or annotated_img is None:
        return
    root = _tk_root_holder.get("root")
    Editor(annotated_img, on_close=None, root=root).show()
```

`do_region_capture` 里的 `on_select` 回调改为新签名：

```python
def on_select(region, annotated_img):
    process_region_selection(region, annotated_img)
```

`RegionSelector(...)` 调用改为 `Selector(...)`，加 `full_img`/`on_copy`/`on_save` 参数：

```python
def open_selector():
    # 选区窗口覆盖整个虚拟桌面（所有屏幕）
    def _on_copy(region, annotated_img):
        if region is None:
            return
        _safe_copy_to_clipboard(annotated_img)

    def _on_save(region, annotated_img):
        if region is None:
            return
        save_image(annotated_img, None)

    Selector(
        x=vx, y=vy, width=vsize[0], height=vsize[1],
        full_img=full,
        on_select=lambda region, img: process_region_selection(region, img),
        on_copy=_on_copy,
        on_save=_on_save,
        root=_tk_root_holder.get("root"),
    ).show()
```

- [ ] **Step 4: 删除 `annotation.py` 和 `tests/test_annotation.py`**

```bash
git rm annotation.py tests/test_annotation.py
```

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/test_app_integration.py tests/test_app.py -v`
Expected: PASS

- [ ] **Step 6: 跑全部测试**

Run: `pytest tests/ -v`
Expected: 全部 PASS（test_annotation.py 已删，其余应通过）

- [ ] **Step 7: Commit**

```bash
git add app.py tests/test_app_integration.py
git rm annotation.py tests/test_annotation.py
git commit -m "refactor: remove AnnotationWindow, process region+annotation in one step"
```

---

### Task 8: 端到端验证（手动）

**Files:**
- 无代码改动，只手动验证

**Interfaces:**
- Consumes: Task 1-7 的全部

- [ ] **Step 1: 启动应用**

```bash
python app.py
```

按 `Ctrl+Shift+X` 触发框选截图。

- [ ] **Step 2: 验证 select 模式**

- 全屏暗化（0.5 alpha 黑色遮罩）
- 十字准星跟随鼠标
- 工具条跟随鼠标，工具按钮**灰色禁用**（不响应点击）
- 尺寸标签显示当前鼠标位置（可选）

- [ ] **Step 3: 验证 adjust 模式**

- 按下左键，出现 1x1 最小框 + 8 个手柄
- 拖拽画框，选区外暗化 + 选区内明亮
- 工具条跟随选区下方，工具按钮**启用**（可点击切换工具）
- 松开左键，可以开始标注（选画笔/箭头/打码等）

- [ ] **Step 4: 验证标注**

- 用画笔在选区内画线
- 用矩形/椭圆/箭头/打码/文字工具标注
- 撤销（Ctrl+Z 或工具条 ↩）
- 调整选区（拖手柄），标注应该被清空

- [ ] **Step 5: 验证确认/取消**

- 按 Enter 或点"✓ 确认" → 进 Editor（保留精修/OCR/保存/关闭）
- 按 Esc 或点"✕ 取消" → 结束，回到桌面
- 点"⧉ 复制" → 复制到剪贴板
- 点"💾 保存" → 保存为文件

- [ ] **Step 6: 验证性能**

- 在 4K 或 5K 多屏环境下拖动选区，工具条跟随应流畅（无卡顿）
- 标注过程中拖动选区手柄，应流畅

- [ ] **Step 7: Commit（如手动验证发现 bug 并修复）**

```bash
git add -A
git commit -m "fix: e2e verification fixes"
```
