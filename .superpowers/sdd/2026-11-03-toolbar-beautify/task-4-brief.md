### Task 4: `selector.py` 改造（浅色工具条）

**Files:**
- Modify: `selector.py`
- Test: `tests/test_selector.py`（适配：按钮改图片后，`test_toolbar_disabled_in_select_mode` / `test_toolbar_enabled_in_adjust_mode` 断言 `fg=` 改为断言 `image=`）

**Interfaces:**
- Consumes: `theme.LIGHT`（浅色 token）, `theme.PALETTE`（色板，从 selector 移入 theme）, `theme.ICON_SIZE`, `theme.FONT`, `theme.FONT_ICON`, `theme.BTN_PAD_X/Y`, `theme.TB_RADIUS`, `theme.SEP_W/H`, `theme.rounded_rect_photo`, `icon.render`.
- Produces: 无新接口；`Selector` 的公共行为不变（`_select_tool` / `_confirm` / `_cancel` / `_copy` / `_save` / `_ocr` / `_undo` 签名不变）。

**改造要点（不改变交互逻辑）：**

1. 顶部 `from theme import LIGHT, PALETTE, FONT, FONT_ICON, ICON_SIZE, BTN_PAD_X, BTN_PAD_Y, TB_RADIUS, SEP_W, SEP_H, rounded_rect_photo` + `import icon`.
2. 删除本地 `TB_BG` / `TB_FG` / `TB_HOVER` / `TB_SEP` / `TB_DISABLED` / `TB_ACTIVE` / `TB_FONT` / `TB_ICON_FONT` / `TB_CONFIRM_FG` / `TB_CANCEL_FG` / `PALETTE` 常量（改引用 theme）。
3. `TOOL_ICONS` 字典保留（作为 icon name 映射），值改为 SVG 文件名（`"brush" → "brush"` 等，与 assets/icons 同名）。
4. `_build_toolbar`：
   - 容器圆角：`tb_win` 内放 `tk.Canvas`（`highlightthickness=0`，铺满），Canvas 上 `create_image` 一张 `rounded_rect_photo(tb_win, w, h, TB_RADIUS, LIGHT["surface"], LIGHT["border"])`，按钮用 Canvas 的 `create_window` 摆放。`tb_win` 的 `<Configure>` 触发重算圆角图尺寸。
   - 工具按钮：`tk.Label(image=icon.render(name, LIGHT["fg"]))`，`padx=BTN_PAD_X, pady=BTN_PAD_Y`. hover 切 `image=icon.render(name, LIGHT["hover_fg"])`（底 `LIGHT["hover"]`）；选中切 `image=icon.render(name, LIGHT["active_fg"])`（底 `LIGHT["active"]`）。
   - 操作按钮（`_tb_btn`）：`tk.Label(image=icon.render(name, fg))`，确认 `fg=theme.DARK["confirm"]` 对应 confirm 绿，取消 `fg=theme.DARK["cancel"]`.
   - 颜色按钮：`icon.render("palette", LIGHT["fg"])`.
   - 粗细 Spinbox：保留，配色改 `bg=LIGHT["surface"], fg=LIGHT["fg"], highlightbackground=LIGHT["sep"]`.
   - 分隔线：`tk.Frame(bar, width=SEP_W, height=SEP_H, bg=LIGHT["sep"])`.
5. `_tb_hover` / `_tb_btn` / `_set_toolbar_enabled`：改为切换 `configure(image=...)`，不再切 `configure(fg=...)` / `configure(text=...)`.
6. 禁用态（select 模式）：图标渲染 `LIGHT["disabled"]` 灰.
7. 尺寸标签 / 十字准星 / 遮罩：不动（`LABEL_BG` / `LABEL_FG` / `CROSSHAIR_COLOR` / `MASK_ALPHA` 保留）。

**测试适配：**
- `test_toolbar_disabled_in_select_mode`：原断言 `c.kwargs.get("fg") == TB_DISABLED`，改为断言 `c.kwargs.get("image") is not None`。
- `test_toolbar_enabled_in_adjust_mode`：同理，断言 `c.kwargs.get("image") is not None`。
- 由于 `icon.render` 在无 Tk 环境返回 mock 的 `PhotoImage`（Task 3 的 mock），`configure(image=...)` 的 kwargs 里 `image` 是 mock 对象，测试可断言 `c.kwargs.get("image") is not None`。
