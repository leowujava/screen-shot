# 合并选区 + 标注：画框即标注

日期：2026-11-01
状态：待批准

## 意图

现在的交互是两步：先框选区域（selector 阶段，工具条只有"确认/取消"），确认后再进入独立的标注窗口（AnnotationWindow，工具条才有完整工具）。用户希望**画框的过程中就出现完整工具条**，画完框直接标注，一次交互完成。

## 目标

- 按下的瞬间出现完整工具条（画笔/矩形/椭圆/箭头/打码/文字 + 颜色 + 粗细 + 撤销 + 确认/复制/保存/取消）
- 工具条跟随选区位置（选区下方，放不下放上方）
- 画完框（松开左键）后，选区外暗化 + 选区内明亮，可以直接开始标注
- 标注过程中工具条继续跟随选区
- 确认 → 进 Editor（保留精修/OCR/保存/关闭）
- 取消 → 结束，回到桌面
- 删除独立的 AnnotationWindow 这一步

## 非目标

- 画框阶段（select→adjust 过渡中）的工具按钮**不响应**（只展示），避免误触；拖拽手柄/移动选区时工具按钮正常响应
- 不做"画框时就能画"的方案 C
- 不改变 Editor 的行为（Editor 仍然负责精修/OCR/保存/关闭）

## 约束

- 仍使用同一个共享 Tk 根窗口（`_tk_root_holder["root"]`），所有 GUI 挂在其上
- 仍用 mss 截屏坐标系（虚拟桌面），与 `capture.py` 一致
- 工具条仍是 `tk.Toplevel` 无边框置顶窗口（`wm_overrideredirect`），不继承 root 的 `-topmost`
- 选区外暗化用 PIL 预渲染的半透明黑色遮罩（alpha=128，与现有 annotation.py 一致）
- 性能：工具条跟随选区时，每帧只走 `geometry()` 移动（SetWindowPos），不触发 Tcl 布局计算

## 方案

### 方案 A：合并两步（选定）

把 selector 和 annotation 合并成一个 `Selector` 类（重写 `selector.py`），内部两个模式：

1. **select 模式**：按下左键前。全屏暗化（0.5 alpha 黑色遮罩），十字准星跟随鼠标，工具条跟随鼠标位置（或固定在屏幕顶部，见下）。
2. **adjust 模式**：按下左键后。出现选区框 + 8 个手柄，可拖动/缩放。选区外暗化 + 选区内明亮。**此时工具条上的工具按钮开始响应**，用户可以直接在选区内标注。
3. **confirm / cancel**：确认进 Editor；取消结束。

工具条位置：
- select 模式：跟随鼠标（在鼠标右下方，边界外回拉）
- adjust 模式：跟随选区下方（与现有 `_tb_show` 逻辑一致）

工具条按钮状态：
- select 模式：所有工具按钮可见但**禁用**（灰色，不响应点击）
- adjust 模式：所有工具按钮**启用**

标注能力：
- 复用现有 `annotation.py` 的绘制逻辑（`draw_brush`/`draw_rect`/`draw_ellipse`/`draw_arrow`/`draw_text`/`mosaic`/`UndoStack`）
- `region_img` 在 adjust 模式开始时从 `full_img` crop 出来
- 调整选区（拖手柄/移动）后重新 crop，清空标注和撤销栈

### 方案 B：selector 阶段只换工具条内容（未选）

selector 的工具条换成完整工具条但工具不可用，确认后才进 AnnotationWindow。
- 缺点：画框阶段工具条是摆设，体验割裂

### 方案 C：画框阶段完全可标注（未选）

画框过程中就能标注。
- 缺点：画框和标注互相干扰，复杂度高

**选定 A**，理由：体验连贯、改动集中、符合飞书/微信截图的交互模式。

## 改动范围

### 删除
- `annotation.py`（合并进 selector）
- `tests/test_annotation.py`（改为测试新的合并逻辑）
- `app.py` 中 `process_region_selection` 里对 `AnnotationWindow` 的调用

### 修改
- `selector.py`：重写为 `Selector` 类，合并选区 + 标注
- `app.py`：
  - `process_region_selection` 改为直接处理 `Selector` 的结果（region + annotated_img），不再打开 AnnotationWindow
  - `do_region_capture` 流程不变（工作线程截屏 → 主线程打开 Selector）
- `tests/test_selector.py`（如有）：更新测试

### 不变
- `editor.py`：Editor 行为不变
- `capture.py`：截屏逻辑不变
- `drawing.py`：绘制原语不变

## 关键设计点

1. **全屏截图**：在 `do_region_capture` 的工作线程中先 `capture_fullscreen()`，把 `full_img` 传给 `Selector`，Selector 内部用 `full_img` 做背景 + crop 选区。
2. **select 模式背景**：全屏 0.5 alpha 黑色遮罩（与现有 selector 一致），不贴 `full_img`（性能考虑，select 阶段不需要显示真实画面）。
3. **adjust 模式背景**：全屏贴 `full_img`，选区外 4 块半透明黑色遮罩（alpha=128），选区内贴 `region_img`（标注画在这上面）。
4. **工具条**：一个 `tk.Toplevel`，包含完整工具。select 模式下工具按钮禁用（灰色），adjust 模式下启用。
5. **确认流程**：`Selector` 的 `on_select` 回调参数改为 `(region, annotated_img)`，`app.py` 直接把它交给 `Editor`。
6. **取消流程**：`on_select(None, None)`。

## 待确认

- 工具条在 select 模式（还没画框）时跟随鼠标，还是固定在屏幕顶部？我倾向**跟随鼠标**，与 adjust 模式一致，体验连贯。
- select 模式下工具按钮是"灰色禁用"还是"完全隐藏"？我倾向**灰色禁用**，让用户提前看到有哪些工具。
