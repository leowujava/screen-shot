"""选区工具条：构建 / 定位 / 颜色 / 工具切换（ToolbarMixin）。

从 selector.py 拆出：方法全部操作 Selector 实例状态（self.tb_win /
self._tb_* / self.tool / self.color 等，定义见 selector.Selector.__init__），
通过多重继承组合，不反向依赖 selector。
"""
from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QColorDialog,
    QFrame,
    QHBoxLayout,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from theme import (
    PALETTE, TB_ACTIVE, TB_BG, TB_CANCEL_FG, TB_CONFIRM_FG,
    TB_DISABLED, TB_FG, TB_HOVER, TB_SEP,
)
from tb_icons import (
    TB_ICON_SIZE, TOOL_ICON_FNS, _tb_icon,
    _paint_icon_cancel, _paint_icon_color, _paint_icon_confirm,
    _paint_icon_copy, _paint_icon_ocr, _paint_icon_save, _paint_icon_undo,
)
from widgets import _Toolbar


class ToolbarMixin:
    """工具条构建与管理（与 Selector 组合使用，见模块 docstring）。"""


    def _build_toolbar(self):
        # 必须是独立顶层窗口（parent=None）：作为 overlay 的子窗口时，
        # Windows 会按父窗口坐标系摆放并裁剪，导致跟随定位错位。
        # self 持有引用，不会被提前 GC。传 self 使工具条可响应
        # Esc/Enter/Ctrl+Z（见 _Toolbar.keyPressEvent）。
        self.tb_win = _Toolbar(self)
        self.tb_win.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.tb_win.setFont(QFont("Microsoft YaHei UI", 10))
        self.tb_win.setStyleSheet(
            f"QWidget {{ background: {TB_BG}; }}"
            f"QToolButton {{ background: transparent; border: none; "
            f"color: {TB_FG}; font-size: 12px; padding: 2px 6px; }}"
            f"QToolButton:hover {{ background: {TB_HOVER}; }}"
            f"QToolButton:disabled {{ color: {TB_DISABLED}; }}"
            f"QSpinBox {{ border: 1px solid {TB_SEP}; background: {TB_BG}; "
            f"color: {TB_FG}; font-size: 10px; padding: 0 2px; }}"
        )

        outer = QVBoxLayout(self.tb_win)
        outer.setContentsMargins(4, 3, 4, 3)
        outer.setSpacing(0)

        bar = QHBoxLayout()
        bar.setSpacing(1)
        outer.addLayout(bar)

        # 工具按钮（矢量图标 + hover 浅灰 + 选中蓝色 / 禁用灰色，见 _refresh_tool_icons）
        for tool in self.TOOLS:
            b = QToolButton(self.tb_win)
            b.setToolTip(self.TOOL_LABELS[tool])
            b.setFixedSize(28, 24)
            b.setIcon(_tb_icon(TOOL_ICON_FNS[tool], color=TB_FG))
            b.setIconSize(QSize(TB_ICON_SIZE, TB_ICON_SIZE))
            b.setToolButtonStyle(Qt.ToolButtonIconOnly)
            b.setStyleSheet(
                f"QToolButton {{ background: transparent; border: none; padding: 0px; }}"
                f"QToolButton:hover {{ background: {TB_HOVER}; }}")
            b.clicked.connect(lambda _checked=False, t=tool: self._select_tool(t))
            bar.addWidget(b)
            self._tool_btns[tool] = b

        bar.addWidget(self._tb_sep())

        # 颜色（单击弹出预设色板，双击打开系统取色器；矢量调色板图标）
        self._tb_icon_btn(bar, "color", _paint_icon_color,
                          "颜色（单击：预设色板；双击：取色器）",
                          self._on_color_clicked)
        self._color_click_timer = None  # 单击/双击判定用（QToolButton 无 doubleClicked 信号）

        # 粗细
        self._spin = QSpinBox(self.tb_win)
        self._spin.setRange(1, 30)
        self._spin.setValue(self.width_attr)
        self._spin.setFixedWidth(56)
        self._spin.editingFinished.connect(self._on_width_change)
        bar.addWidget(self._spin)

        # 撤销
        self._tb_icon_btn(bar, "undo", _paint_icon_undo, "撤销", self._undo)

        bar.addWidget(self._tb_sep())

        # 识别文字（矢量图标：文本行 + 放大镜，替代模糊的 🔍 emoji）
        self._tb_icon_btn(bar, "ocr", _paint_icon_ocr, "识别文字", self._ocr)

        bar.addWidget(self._tb_sep())

        # 尾部动作（顺序对齐参考设计）：复制 / 保存(下载) / 取消(红 X) / 确认(绿 ✓)
        self._tb_icon_btn(bar, "copy", _paint_icon_copy, "复制", self._copy)
        self._tb_icon_btn(bar, "save", _paint_icon_save, "保存", self._save)
        self._tb_icon_btn(bar, "cancel", _paint_icon_cancel, "取消", self._cancel,
                          color=TB_CANCEL_FG)
        self._tb_icon_btn(bar, "confirm", _paint_icon_confirm, "确认", self._confirm,
                          color=TB_CONFIRM_FG)

        # 预设色板行（默认收起，点 🎨 弹出）
        self._color_row = QWidget(self.tb_win)
        row = QHBoxLayout(self._color_row)
        row.setContentsMargins(2, 0, 2, 0)
        row.setSpacing(4)
        for c in PALETTE:
            b = QToolButton(self._color_row)
            b.setFixedSize(16, 16)
            b.setStyleSheet(self._swatch_style(c, selected=False))
            b.clicked.connect(lambda _checked=False, col=c: self._set_color(col))
            row.addWidget(b)
            self._color_btns[c] = b
        self._color_row.setVisible(False)
        outer.addWidget(self._color_row)

    def _swatch_style(self, color, selected):
        border = f"2px solid {TB_ACTIVE}" if selected else "1px solid #c9cdd4"
        return f"QToolButton {{ background: {color}; border: {border}; }}"

    def _tb_sep(self, bar=None):
        f = QFrame(self.tb_win)
        f.setFrameShape(QFrame.VLine)
        f.setFixedHeight(18)
        f.setStyleSheet(f"background: {TB_SEP}; color: {TB_SEP};")
        if bar is not None:
            bar.addWidget(f)
        return f

    def _tb_icon_btn(self, bar, key, paint_fn, tip, command, color=TB_FG):
        """工具条矢量图标按钮。

        独立于文字按钮：用 QPainter 高倍绘制图标（见 _tb_icon），不依赖
        emoji/特殊字体渲染，任意 DPI 下都清晰。padding 置零避免工具条全局
        样式（2px 6px）把图标裁掉。color 指定线稿颜色（确认绿/取消红等）。
        """
        b = QToolButton(self.tb_win)
        b.setFixedSize(28, 24)
        b.setToolTip(tip)
        b.setIcon(_tb_icon(paint_fn, color=color))
        b.setIconSize(QSize(TB_ICON_SIZE, TB_ICON_SIZE))
        b.setToolButtonStyle(Qt.ToolButtonIconOnly)
        b.setStyleSheet(
            f"QToolButton {{ background: transparent; border: none; padding: 0px; }}"
            f"QToolButton:hover {{ background: {TB_HOVER}; }}")
        b.clicked.connect(command)
        bar.addWidget(b)
        self._tb_btns[key] = b
        return b

    def _measure_toolbar(self):
        """测量工具条实际尺寸（颜色行弹出/收起会改变窗口尺寸）。

        只在首次显示或行状态变化时调用，避免每帧拖动都触发同步布局计算
        （拖动性能回归测试的根因）。
        """
        self.tb_win.adjustSize()
        self._tb_size = (self.tb_win.width(), self.tb_win.height())

    def _on_color_clicked(self):
        """颜色按钮点击：单击（300ms 内无第二次点击）弹出预设色板，
        双击直接打开系统取色器。"""
        if self._color_click_timer is None:
            self._color_click_timer = QTimer(self.tb_win)
            self._color_click_timer.setSingleShot(True)
            self._color_click_timer.setInterval(300)
            self._color_click_timer.timeout.connect(self._on_color_single_click)
            self._color_click_timer.start()
        else:
            self._color_click_timer.stop()
            self._color_click_timer = None
            self._pick_color()

    def _on_color_single_click(self):
        """单击超时：复位计时器句柄后再弹板。

        必须复位：否则下一次单击会看到残留的 timer 句柄，被误判成
        "双击"，直接打开系统取色器，预设色板再也弹不出来。
        """
        self._color_click_timer = None
        self._toggle_color_row()

    def _toggle_color_row(self):
        """在工具条内弹出/收起预设色板行（仿工具条截图）。"""
        self._color_row.setVisible(not self._color_row.isVisible())
        self._update_color_row()
        # 行状态变化 → 重新测量并（若可见）重新定位
        if self._tb_visible and self._tb_region is not None:
            self._tb_show(self._tb_region, (self._tb_region[0], self._tb_region[1],
                                            self._tb_region[2] - self._tb_region[0],
                                            self._tb_region[3] - self._tb_region[1]))
        elif self.tb_win.isVisible():
            self._measure_toolbar()

    def _close_color_row(self):
        if self._color_row is not None:
            self._color_row.setVisible(False)

    def _set_color(self, color):
        self.color = color
        self._update_color_row()
        # 颜色变化后重渲染当前标注（线条/文字颜色即时生效）
        if self._region_img is not None and self._mode == "adjust":
            self._render_all()

    def _update_color_row(self):
        """给当前选中颜色的色块加蓝圈高亮。"""
        for c, b in self._color_btns.items():
            b.setStyleSheet(
                self._swatch_style(c, selected=(c.lower() == self.color.lower())))

    def _tb_show(self, pos, size):
        """定位/显示工具条。pos = (l, t, r, b)，size = (l, t, w, h)。

        首次显示：先（隐藏状态下）测量尺寸，再直接以最终几何 show，
        避免窗口在默认位置以空内容闪现一帧（白底闪烁）。
        后续帧：只 setGeometry 移动，不重新布局。
        """
        self._tb_region = (pos[0], pos[1], pos[2], pos[3])
        tb = self.tb_win
        if not self._tb_visible:
            self._measure_toolbar()
            tb.show()
            self._tb_visible = True
        row_state = 1 if self._color_row.isVisible() else 0
        if self._tb_row_state != row_state:
            self._measure_toolbar()
            self._tb_row_state = row_state
        x, y = pos[0], pos[1]
        h = size[3]
        tb_w, tb_h = self._tb_size
        W, H = self._actual_w, self._actual_h
        ty = y + h + 4
        if ty + tb_h > H:
            ty = y - tb_h - 4
        ty = max(0, min(ty, H - tb_h))
        tx = max(0, min(x, W - tb_w)) if tb_w else x
        screen_x = self._logical_origin_x + tx
        screen_y = self._logical_origin_y + ty
        tb.setGeometry(screen_x, screen_y, tb_w, tb_h)
        # 缓存工具条屏幕位置（供 _is_over_toolbar 使用，避免每帧查询窗口几何）
        self._tb_pos = (screen_x, screen_y, tb_w, tb_h)

    def _tb_hide(self):
        self._tb_visible = False
        self._tb_size = None
        self._tb_row_state = 0
        self._tb_pos = None
        self._close_color_row()
        if self.tb_win is not None:
            try:
                self.tb_win.hide()
            except Exception:
                pass

    def _is_over_toolbar(self, cx, cy):
        """判断 canvas 坐标 (cx, cy) 是否落在工具条窗口范围内。

        用缓存的工具条位置（_tb_pos），避免每帧查询窗口几何。
        _tb_pos 在 _tb_show 中更新。
        """
        if self.tb_win is None or not self._tb_visible:
            return False
        pos = self._tb_pos
        if pos is None:
            return False
        tx, ty, tw, th = pos
        if tw <= 1 or th <= 1:
            return False
        sx = cx + self._logical_origin_x
        sy = cy + self._logical_origin_y
        return tx <= sx <= tx + tw and ty <= sy <= ty + th

    def _set_toolbar_enabled(self, enabled):
        """启用/禁用工具按钮。禁用时灰色 + 不响应。"""
        for b in self._tool_btns.values():
            b.setEnabled(enabled)
        self._update_tool_button_styles()

    def _update_tool_button_styles(self):
        """选中工具蓝色高亮，其余恢复正常色（矢量图标通过重绘换色）。"""
        for tool, b in self._tool_btns.items():
            b.setProperty("active", tool == self.tool)
        self._refresh_tool_icons()

    def _refresh_tool_icons(self):
        """按状态重绘各工具按钮图标：禁用=灰、选中=蓝、其余=深色。"""
        for tool, b in self._tool_btns.items():
            if not b.isEnabled():
                col = TB_DISABLED
            elif tool == self.tool:
                col = TB_ACTIVE
            else:
                col = TB_FG
            b.setIcon(_tb_icon(TOOL_ICON_FNS[tool], color=col))

    def _select_tool(self, name):
        self.tool = name
        self._tb_active_tool = name
        # 不 activateWindow / 不抢焦点（对齐旧版"只重设 topmost、不 lift"）：
        # 工具条的置顶已在窗口标志中，切换工具只更新按钮高亮。
        self._update_tool_button_styles()
        self._update_cursor()

    def _pick_color(self):
        col = QColorDialog.getColor(QColor(self.color), self, "选择颜色")
        if col.isValid():
            self.color = col.name()
            self._update_color_row()
            # 颜色变化后重渲染当前标注
            if self._region_img is not None and self._mode == "adjust":
                self._render_all()

    def _on_width_change(self):
        try:
            val = int(self._spin.value())
            self.width_attr = max(1, min(30, val))
        except Exception:
            pass
