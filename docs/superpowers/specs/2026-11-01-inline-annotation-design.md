# 选区后内联标注窗口（Approach B）设计

## 意图与背景

当前流程：框选 → 工具条只有"确认/取消" → 确认打开独立 Editor 窗口编辑。与飞书的核心差距是**不能直接在选区上标注**，必须先进编辑器。

本次目标：框选完成后，不直接打开 Editor，而是打开一个**全屏不透明标注窗口**——全屏截图做背景、选区外暗化、选区内明亮、工具条支持箭头/矩形/画笔/文字/马赛克/颜色/粗细/撤销/确认/复制/保存/取消。标注完成后：
- "✓ 确认" → 标注结果交给现有 Editor（精修、OCR、保存能力保留）
- "复制" → 复制到剪贴板，结束
- "保存" → 存桌面，结束
- "取消" → 结束，无输出

## 方案（已选 B：独立标注窗口）

- **B. 两阶段——选区覆盖层 → 独立标注窗口**（已选）：框选在透明覆盖层完成；确认后关闭覆盖层，打开一个全屏不透明标注窗口，显示全屏截图 + 选区暗化 + 工具条。标注在该窗口进行。
  - 优点：复用 editor.py 的绘制逻辑；不透明窗口无 alpha/透明度坑；选区可调整（标注前）；
  - 代价：比 Approach A（覆盖层内联标注）多一次窗口切换，但几乎瞬时（region select → annotation window 都是同一主循环驱动）。
- A. 覆盖层内联标注（spec 原方案）：透明覆盖层上直接画。已否——透明窗口 + 4 块暗化遮罩 + PhotoImage 生命周期管理复杂，alpha 坑多。
- C. 工具条只加按钮、委托 Editor：已否——不是"内联"，用户还是离开覆盖层，与飞书差异大。

## 数据流

```
Ctrl+Shift+X
  → worker 线程：capture_fullscreen() 抓全屏截图
  → 主线程：RegionSelector 覆盖层（透明，框选 + 调整手柄）
  → 用户拖完选区，按"✓ 确认"（或 Enter / 双击）
  → 覆盖层关闭，on_select(region, full) 回调
  → 主线程：打开 AnnotationWindow（全屏不透明）
       背景 = full（全屏截图）
       选区外 = 4 块半透明暗化遮罩
       选区内 = 明亮（无遮罩）
       工具条 = 箭头/矩形/画笔/文字/马赛克 + 颜色 + 粗细 + 撤销 + 确认/复制/保存/取消
  → 用户标注（画在选区图上，可撤销）
  → "✓ 确认" → Editor(annotated_region) 打开（保留精修/OCR/保存）
       或 "复制" → 剪贴板，结束
       或 "保存" → 桌面文件，结束
       或 "取消" → 结束，无输出
```

## 模块改动

### `drawing.py`（新文件）

从 editor.py 抽出的共享绘制函数，纯 PIL 操作，不依赖 Tk：

- `draw_arrow(img, start, end, color, width)` — 从 editor.py 的 `_draw_arrow` 抽出
- `draw_rect(img, box, color, width)` — outline 矩形（从 editor.py `_on_release` 的 rect 分支抽出）
- `draw_ellipse(img, box, color, width)` — outline 椭圆（从 editor.py `_on_release` 的 ellipse 分支抽出）
- `draw_brush(img, points, color, width)` — 折线画笔（从 editor.py `_on_drag` 的 brush 逻辑抽出，一次画整段）
- `draw_text(img, pos, text, color, font_size)` — 文字（从 editor.py `_input_text` 的 commit 抽出）
- `mosaic(img, left, top, right, bottom, block=8)` — 从 editor.py 移过来（已有，直接搬）
- `UndoStack` — 从 editor.py 移过来（纯数据结构，已有）

### `editor.py`（小改）

- `mosaic` / `UndoStack` 改为 `from drawing import mosaic, UndoStack`（re-export，保证现有 `test_editor.py` 的 `from editor import UndoStack, mosaic` 继续通过）
- `_draw_arrow` / `_on_release` 的 rect/ellipse/brush 分支改为调用 drawing.py 的函数
- `_input_text` 的 commit 改为调用 `draw_text`
- 其余不变（编辑器行为完全保留）

### `annotation.py`（新文件）

新的 `AnnotationWindow` 类，全屏不透明标注窗口：

```python
class AnnotationWindow:
    def __init__(self, full_img, region, on_confirm, on_copy, on_save, on_cancel, root=None):
        # full_img: PIL Image，全屏截图
        # region: (left, top, width, height)，虚拟桌面坐标
        # on_confirm(annotated_img): 确认回调，annotated_img 是标注后的选区 PIL Image
        # on_copy(annotated_img): 复制回调
        # on_save(annotated_img): 保存回调
        # on_cancel(): 取消回调
        # root: 共享 Tk 根窗口（Toplevel，非阻塞）

    # 内部状态
    self.full_img          # 全屏截图（背景）
    self.region            # (left, top, w, h)
    self.region_img        # 选区 crop（PIL Image，标注画在这上面）
    self.original_region   # 选区 crop 的原始副本（撤销用）
    self.tool              # 当前工具
    self.color, self.width # 标注属性
    self.undo = UndoStack(10)
    self.drawing, self.draw_start, self._brush_last, self._preview_id
```

**窗口布局**：
- 全屏不透明 Toplevel（覆盖虚拟桌面，与 selector 相同的 Win32 强制扩展逻辑）
- Canvas 铺满，背景 = 全屏截图（用 ImageTk.PhotoImage 贴一次，不随标注变化）
- 4 块暗化遮罩：用 PIL 预渲染 4 块黑色半透明图（alpha=128），贴在 4 个角落（选区外）。选区变化时重建。
- 选区边框：白色 2px 矩形
- 8 个手柄：可拖动调整选区（与 selector 相同的手柄逻辑，复用）
- 工具条：选区下方，深色，含工具/颜色/粗细/撤销/确认/复制/保存/取消

**标注流程**：
- 点选工具 → 在选区内按下/拖动 → 实时预览（canvas 临时元素）→ 松开后画到 region_img → 更新选区 PhotoImage
- 画笔：连续绘制，每段 update PhotoImage
- 矩形/椭圆/箭头/马赛克：拖拽预览，松开后画到 region_img
- 文字：点击选区内位置弹输入框，回车后画到 region_img
- 撤销：Ctrl+Z + 工具条 ↩，限 10 步，只撤销标注不影响选区
- **调整选区后标注作废**：移动/缩放手柄使标注作废（标注烘焙在旧 crop 上），清空标注、从 full_img 重新 crop region_img、清空撤销栈（与飞书行为一致）

**结果输出**：
- "✓ 确认" → on_confirm(region_img)（region_img 是标注后的选区 PIL Image）
- "复制" → on_copy(region_img)
- "保存" → on_save(region_img)
- "取消" → on_cancel()
- 取消/复制/保存后，AnnotationWindow 关闭（withdraw + 清理）

### `selector.py`（中改）

- `on_select` 回调签名不变：`on_select(region)`，region 为 (left, top, w, h) 虚拟桌面坐标
- 工具条不变（确认/取消 + 尺寸）
- 确认时调用 `self._finish(region)`，由 app.py 的 on_select 回调决定打开 AnnotationWindow
- 不需要改渲染逻辑（覆盖层仍只做框选 + 调整，不做标注）

### `app.py`（中改）

- `do_region_capture` 的 worker 线程已抓到 `full`（全屏截图），传给 `on_select`
- `process_region_selection(full, region)`：
  - region 为 None → 不做任何事
  - region 有效 → 打开 AnnotationWindow(full, region, on_confirm, on_copy, on_save, on_cancel, root)
  - on_confirm(annotated_img) → Editor(annotated_img, on_close=None, root).show()（保留现有编辑器）
  - on_copy(annotated_img) → copy_to_clipboard(annotated_img)，结束
  - on_save(annotated_img) → save_image(annotated_img)，结束
  - on_cancel() → 结束

## 坐标与渲染

- 全屏截图 `full_img` 尺寸 = 虚拟桌面尺寸（与 selector 的 `_actual_w/_actual_h` 一致）
- Canvas 坐标 = 虚拟桌面坐标（与 selector 相同映射）
- 选区 region (l, t, r, b) 在 canvas 上的位置 = (l, t) 到 (r, b)
- 选区图 `region_img` = `full_img.crop((l, t, r, b))`，尺寸 = (r-l) x (b-t)
- 标注事件坐标 → 选区图坐标：`x - l, y - t`
- 暗化遮罩：4 块 PIL 图（黑 + alpha=128），尺寸随选区变化，用 create_image 贴在 4 个角落
  - 左上: (0, 0) 到 (l, t)
  - 右上: (r, 0) 到 (W, t)
  - 左下: (0, b) 到 (l, H)
  - 右下: (r, b) 到 (W, H)
- 性能：全屏背景图只贴一次；标注时只更新选区图 PhotoImage + 临时预览元素；选区变化时重建 4 块遮罩图

## 键盘

- 框选/调整/标注阶段：Esc 取消，Enter = "✓ 确认"（打开编辑器），Ctrl+Z 撤销标注
- 数字键 1-5 选工具（可选，若实现成本低）

## 边界情况

- 选区被缩到无效（< 5px）→ 回退 select 模式，标注层隐藏（已有逻辑，保留）
- 标注后撤销到空 → region_img 恢复为原始 crop
- 文字输入框打开时按 Esc → 只关输入框，不取消标注窗口
- 工具条超出屏幕下边缘 → 放选区上方（沿用现有 `_tb_show` 回退逻辑）
- 多显示器：选区图/遮罩/工具条都基于 canvas 坐标，与 selector 的 `_to_virtual` 换算一致

## 测试

- `test_drawing.py`（新）：drawing.py 各函数的 PIL 输出（尺寸、像素内容）
- `test_annotation.py`（新）：
  - AnnotationWindow 的标注 → 撤销 状态机流转（mock Tk，延续 fake_root 模式）
  - region_img 裁剪/标注/撤销后的像素结果
  - 工具条按钮回调（确认/复制/保存/取消）
  - 调整选区后标注作废（重新 crop + 清空撤销栈）
- `test_editor.py`：现有测试应继续通过（editor 改为 import drawing，行为不变）
- `test_selector.py`：现有测试应继续通过（selector 渲染逻辑不变）
- 手动验证：跑应用，框选 → 标注 → 确认/复制/保存/取消

## 风险

- `annotation.py` 变大（预计 ~600 行），工具条 + 标注 + 遮罩三套渲染逻辑要清晰分层
- 半透明遮罩：PIL 预渲染 4 块图，选区变化时重建，需控制重建频率（只在调整/缩放时重建，标注时不动）
- 全屏截图 `full_img` 可能很大（5760x2160），PhotoImage 转换开销大，但只转一次，可接受
- 文字工具在标注窗口内弹输入框，与编辑器 `_input_text` 模式一致，复用逻辑

## 实施顺序

1. `drawing.py`（新）+ `test_drawing.py`（新）+ editor.py 改为 import drawing
2. `annotation.py`（新）+ `test_annotation.py`（新）
3. `app.py` 改 process_region_selection，接入 AnnotationWindow
4. 手动验证全流程
