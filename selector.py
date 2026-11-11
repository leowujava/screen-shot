"""选区 + 标注（PySide6 实现）。

合并选区 + 标注：拖拽框选 → 完整工具条 → 画完框直接标注 → 确认/复制/保存/取消。

窗口模型（与旧 Tk 版一致）：
- overlay：无边框置顶窗口，覆盖整个虚拟桌面（所有屏幕）。
  full_img 为 None 时窗口透明度 0.5（黑底透出桌面暗化效果）；
  首次框选完成时捕获并定格整屏，之后按 1.0 不透明渲染定格截图。
- toolbar：独立的无边框置顶窗口，跟随选区定位（选区下方，放不下翻到上方）。

内部两个模式：
- select：按下左键前。全屏暗化，十字准星跟随鼠标，工具条工具按钮禁用。
- adjust：按下左键后。出现选区框 + 8 个手柄，可拖动/缩放/标注。

渲染方式：所有画面（背景 / 暗化 / 选区图 / 边框 / 手柄 / 十字准星 / 预览）
都在 overlay 的 paintEvent 中由 QPainter 一次绘制，状态（self._region 等）
是唯一事实来源，重绘是纯函数式的，无需管理画布元素生命周期。
"""
import ctypes
import math
import sys
import threading

from PIL import Image, ImageDraw

from PySide6.QtCore import QEvent, QRect, QRectF, Qt, Signal, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QImage,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QMessageBox,
)

from drawing import (
    draw_arrow, draw_rect, draw_ellipse, draw_brush, draw_text, mosaic,
    UndoStack, get_ocr_engine, ocr_result_to_text, alt_pressed,
)

# 选区完成回调常量（保留向后兼容）
SELECT_DONE = "done"      # 用户确认选区（Enter / 双击 / 工具条确认）
SELECT_CANCEL = "cancel"  # 用户取消（Esc / 右键 / 工具条取消）

# 工具条配色（白底 + 蓝色高亮，仿飞书截图工具条）
TB_BG = "#ffffff"
TB_FG = "#1f2329"
TB_HOVER = "#f2f3f5"        # 按钮 hover 浅灰
TB_SEP = "#e5e6eb"
TB_DISABLED = "#c9cdd4"     # 禁用（select 模式）
TB_ACTIVE = "#4a90e2"       # 选中工具（蓝圈/蓝字）

# 确认 / 取消 图标颜色
TB_CONFIRM_FG = "#22c55e"
TB_CANCEL_FG = "#f5484d"

# 预设色板（仿工具条下方弹出的颜色行；第一色 = 默认画图色，截图箭头红）
PALETTE = ["#f5484d", "#3370ff", "#86909c",
           "#4e5969", "#ffab00", "#22c55e",
           "#3490ff", "#1f2329", "#a0a6b0", "#ffffff"]

# 尺寸标签配色
LABEL_BG = "#2b2f36"
LABEL_FG = "#f5f6f8"

# 选区边框配色：白色外线 + 深色内线（双线）。
# 纯白边框在白色截图背景上不可见，双线保证任意背景下至少一条线可见。
BORDER_OUTLINE = "white"
BORDER_INNER = "#4e5969"

# 十字准星颜色
CROSSHAIR_COLOR = "red"
CROSSHAIR_WIDTH = 1

# 暗化遮罩 alpha（0-255，128 ≈ 50%）
MASK_ALPHA = 128

# 无定格截图时的窗口透明度（与旧 Tk 版 attributes("-alpha", 0.5) 一致）
DIM_WINDOW_OPACITY = 0.5
FULL_WINDOW_OPACITY = 1.0


def _virtual_screen_size():
    """返回虚拟桌面（所有显示器合并）的 (width, height)。失败返回 (0, 0)。"""
    app = QGuiApplication.instance()
    if app is not None:
        try:
            g = app.primaryScreen().virtualGeometry()
            if g.width() > 0 and g.height() > 0:
                return g.width(), g.height()
        except Exception:
            pass
    if sys.platform == "win32":
        try:
            user32 = ctypes.windll.user32
            w = user32.GetSystemMetrics(78)
            h = user32.GetSystemMetrics(79)
            if w > 0 and h > 0:
                return w, h
        except Exception:
            pass
    return 0, 0


def _pil_to_qpixmap(img):
    """PIL Image → QPixmap（不拷贝 img，转换一次缓存使用）。"""
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    data = img.tobytes("raw", "RGBA")
    return QPixmap.fromImage(QImage(data, img.width, img.height,
                                    QImage.Format_RGBA8888))


def _event_pos(event):
    """取鼠标事件的 widget 坐标 (x, y)。

    兼容真实 QMouseEvent（position()）与测试里只带 .x/.y 的自定义 Event。
    """
    pos = getattr(event, "position", None)
    if callable(pos):
        try:
            p = pos()
            return int(p.x()), int(p.y())
        except Exception:
            pass
    return int(event.x), int(event.y)


class _Toolbar(QWidget):
    """选区工具条：独立置顶无边框窗口。

    键盘（Esc / Enter / Ctrl+Z）与 overlay 保持一致；
    closeEvent 被忽略，工具条的显示/隐藏完全由 Selector 管理。
    """

    def __init__(self, selector):
        super().__init__()
        self._selector = selector

    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key_Escape:
            self._selector._cancel()
        elif k in (Qt.Key_Return, Qt.Key_Enter):
            self._selector._confirm()
        elif k == Qt.Key_Z and (e.modifiers() & Qt.ControlModifier):
            self._selector._undo()
        else:
            super().keyPressEvent(e)

    def closeEvent(self, e):
        e.ignore()


class _OcrResultDialog(QDialog):
    """OCR 结果弹窗：只读文本可选中，支持复制全部 / 复制选中 / Ctrl+C。"""

    def __init__(self, text, parent):
        super().__init__(parent)
        self.setWindowTitle("识别文字")
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.resize(520, 360)

        layout = QVBoxLayout(self)
        self.text_edit = QTextEdit(self)
        self.text_edit.setReadOnly(True)
        self.text_edit.setLineWrapMode(QTextEdit.WidgetWidth)
        self.text_edit.setText(text)
        layout.addWidget(self.text_edit)

        bar = QHBoxLayout()

        def copy_selected():
            sel = self.text_edit.selectedText()
            if sel:
                from capture import copy_text_to_clipboard
                if copy_text_to_clipboard(sel):
                    self.accept()

        def copy_all():
            from capture import copy_text_to_clipboard
            if copy_text_to_clipboard(text):
                self.accept()

        btn_sel = QPushButton("复制选中")
        btn_sel.clicked.connect(copy_selected)
        btn_all = QPushButton("复制全部")
        btn_all.clicked.connect(copy_all)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.reject)
        bar.addStretch(1)
        bar.addWidget(btn_sel)
        bar.addWidget(btn_all)
        bar.addWidget(btn_close)
        layout.addLayout(bar)

    def keyPressEvent(self, e):
        # Ctrl+C 复制选中（capture.copy_text_to_clipboard，与剪贴板行为一致）
        if e.key() == Qt.Key_C and (e.modifiers() & Qt.ControlModifier):
            sel = self.text_edit.selectedText()
            if sel:
                from capture import copy_text_to_clipboard
                copy_text_to_clipboard(sel)
            return
        super().keyPressEvent(e)


class Selector(QWidget):
    """合并选区 + 标注：拖拽框选 → 完整工具条 → 画完框直接标注 → 确认/复制/保存/取消。

    内部两个模式：
    - select：按下左键前。全屏暗化，十字准星跟随鼠标，工具条工具按钮禁用。
    - adjust：按下左键后。出现选区框 + 8 个手柄，可拖动/缩放/标注。

    x, y: 窗口左上角在虚拟桌面中的坐标（使窗口 (0,0) 对齐虚拟桌面 (0,0)）
    width, height: 虚拟桌面尺寸
    full_img: 全屏截图（PIL Image），用于 adjust 模式背景和选区 crop；
              为 None 时首次框选完成后用 capture_screen 捕获并定格画面。
    on_select: 确认/取消回调，参数为 (region, annotated_img)；取消时 (None, None)
      region = (left, top, width, height) mss 虚拟桌面坐标
      annotated_img = PIL Image（选区 crop + 标注）
    on_copy: 复制回调，签名同 on_select
    on_save: 保存回调，签名同 on_select
    root: 兼容旧 Tk 版签名（共享根窗口）；PySide6 版忽略该参数，
          窗口统一由 Qt 事件循环驱动，show() 始终非阻塞。
    capture_screen: 可选的整屏截图函数（无参，返回 PIL Image），
                    full_img 为 None 时框选完成后调用。
    """

    MIN_SIZE = 5
    HANDLE_SIZE = 12
    MARGIN = 8

    # 8 个手柄的标识
    HANDLES = ("nw", "n", "ne", "e", "se", "s", "sw", "w")

    # 各手柄的窗口光标（拖动方向提示）
    _CURSORS = {"nw": Qt.SizeFDiagCursor, "n": Qt.SizeVerCursor,
                "ne": Qt.SizeBDiagCursor, "e": Qt.SizeHorCursor,
                "se": Qt.SizeFDiagCursor, "s": Qt.SizeVerCursor,
                "sw": Qt.SizeBDiagCursor, "w": Qt.SizeHorCursor,
                "move": Qt.SizeAllCursor}

    # 标注工具（取色已集成到颜色面板，不在主工具条）
    TOOLS = ("move", "brush", "rect", "ellipse", "arrow", "mosaic", "text")
    TOOL_ICONS = {"move": "✥", "brush": "✏", "rect": "⬜", "ellipse": "◯", "arrow": "↗",
                  "mosaic": "▒", "text": "T"}
    TOOL_LABELS = {"move": "移动选区", "brush": "画笔", "rect": "矩形", "ellipse": "圆形",
                   "arrow": "箭头", "mosaic": "打码", "text": "文字"}

    # OCR 线程 → 主线程（跨线程信号自动排队投递到主线程）
    _ocr_text_ready = Signal(str)
    _ocr_failed = Signal(str)

    def __init__(self, x, y, width, height, full_img, on_select,
                 on_copy, on_save, root=None, capture_screen=None):
        super().__init__()
        # 注意：不能叫 self.x/y/width/height——会覆盖 QWidget 同名方法
        self.origin_x = x
        self.origin_y = y
        self.virtual_width = width
        self.virtual_height = height
        self.full_img = full_img
        self.capture_screen = capture_screen
        self.on_select = on_select
        self.on_copy = on_copy
        self.on_save = on_save
        self.result = None
        self._mode = "select"
        self._region = None
        self._drag_op = None
        self._drag_start = None
        self._drag_region = None
        self._dragging_box = False
        self._crosshair = None  # (x, y, w, h) 或 None；w/h 为 0 时不显示尺寸标签
        self._preview = None    # 标注预览 {"tool": ..., "box": ...} / {"tool": "arrow", ...}
        self._masks_hidden = False
        self._base_pixmap = None
        self._region_pixmap = None
        self._tb_visible = False
        self._tb_size = None
        self._tb_row_state = 0
        self._tb_pos = None
        self._tb_region = None
        self._tb_active_tool = "move"
        self._color_btns = {}
        self._cursor = Qt.CrossCursor
        self._finished = False
        self._pending_callback = None

        # 标注状态（默认颜色 = 色板第一色，与截图中箭头红色一致）
        self.tool = "move"
        self.color = PALETTE[0]
        self.width_attr = 3
        # 撤销深度上限：每笔标注都保存一份选区图副本。上限过低（如 10）时，
        # 画满上限后最旧快照被挤出，撤销到栈空也回不到干净底图（"撤销没干净"）。
        # 50 笔足以覆盖正常标注量，且内存可控（每份 ≈ 选区尺寸 × 3 字节）。
        self.undo = UndoStack(maxlen=50)
        self.drawing = False
        self.draw_start = None
        self.start = None  # 框选起点（select→adjust 过渡阶段）
        self._brush_last = None
        self._tool_btns = {}
        self._tb_btns = {}
        self._region_img = None

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

        # 动态计算实际屏幕大小（虚拟桌面）
        self._actual_w, self._actual_h = _virtual_screen_size()
        if self._actual_w <= 0 or self._actual_h <= 0:
            # 查询失败（如测试环境）：退回调用方传入的尺寸
            self._actual_w, self._actual_h = width, height
        self._scale_x = width / self._actual_w if self._actual_w else 1.0
        self._scale_y = height / self._actual_h if self._actual_h else 1.0

        self.setGeometry(x, y, self._actual_w, self._actual_h)

        if self.full_img is not None:
            self._base_pixmap = _pil_to_qpixmap(self.full_img)
        self._apply_window_opacity()

        self._build_toolbar()
        # 监听自身激活事件：overlay 被激活（点击选区/回到前台）时会提到置顶层最前，
        # 可能盖住已显示的工具条，需在激活后把工具条重新 raise 回来（见 eventFilter）。
        self.installEventFilter(self)
        # 初始 select 模式：工具按钮禁用
        self._set_toolbar_enabled(False)
        self._finished = False

        self._ocr_text_ready.connect(self._show_ocr_result)
        self._ocr_failed.connect(self._on_ocr_failed)

    # ------------------------------------------------------------------ #
    # 窗口
    # ------------------------------------------------------------------ #

    def _apply_window_opacity(self):
        """无定格截图时窗口半透明（黑底透出桌面）；定格后不透明。"""
        self.setWindowOpacity(DIM_WINDOW_OPACITY if self.full_img is None
                              else FULL_WINDOW_OPACITY)

    def _force_focus(self):
        """把键盘焦点强制拉回 overlay（重映射窗口后焦点可能落到之前的应用）。"""
        self.activateWindow()
        self.setFocus()

    def show(self):
        """显示选区窗口（非阻塞，由 Qt 事件循环驱动）。"""
        super().show()
        self.raise_()
        self.activateWindow()
        self.setFocus()

    def eventFilter(self, obj, event):
        """overlay 成为活动窗口时把工具条重新提到最前。

        overlay 与工具条同为置顶层（WindowStaysOnTopHint）。点选区/标注/回到前台
        都会激活 overlay，Windows 会把它提到置顶层最前——全屏 overlay 随即盖住
        工具条（表现为"拖送选框时工具栏消失"）。PySide6 未暴露可重写的
        windowActivateEvent，故用事件过滤器监听 WindowActivate 事件，
        用 raise_() 只调整置顶层内 Z 序、不抢键盘焦点。
        """
        if obj is self and event.type() == QEvent.Type.WindowActivate:
            if self._tb_visible and self.tb_win is not None and self.tb_win.isVisible():
                self.tb_win.raise_()
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------ #
    # 工具条
    # ------------------------------------------------------------------ #

    def _build_toolbar(self):
        # 必须是独立顶层窗口（parent=None）：作为 overlay 的子窗口时，
        # Windows 会按父窗口坐标系摆放并裁剪，导致跟随定位错位。
        # self 持有引用，不会被提前 GC。
        self.tb_win = _Toolbar(None)
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

        # 工具按钮（图标 + hover 浅灰 + 选中蓝色）
        for tool in self.TOOLS:
            b = QToolButton(self.tb_win)
            b.setText(self.TOOL_ICONS[tool])
            b.setToolTip(self.TOOL_LABELS[tool])
            b.setFixedSize(28, 24)
            b.clicked.connect(lambda _checked=False, t=tool: self._select_tool(t))
            bar.addWidget(b)
            self._tool_btns[tool] = b

        bar.addWidget(self._tb_sep())

        # 颜色（单击弹出预设色板，双击打开系统取色器）
        color_btn = QToolButton(self.tb_win)
        color_btn.setText("🎨")
        color_btn.setFixedSize(28, 24)
        color_btn.setToolTip("颜色（单击：预设色板；双击：取色器）")
        color_btn.clicked.connect(self._on_color_clicked)
        bar.addWidget(color_btn)
        self._tb_btns["color"] = color_btn
        self._color_click_timer = None  # 单击/双击判定用（QToolButton 无 doubleClicked 信号）

        # 粗细
        self._spin = QSpinBox(self.tb_win)
        self._spin.setRange(1, 30)
        self._spin.setValue(self.width_attr)
        self._spin.setFixedWidth(56)
        self._spin.editingFinished.connect(self._on_width_change)
        bar.addWidget(self._spin)

        # 撤销
        undo_btn = self._tb_btn(bar, "undo", "↩", "撤销", self._undo)

        bar.addWidget(self._tb_sep())

        # 识别文字
        self._tb_btn(bar, "ocr", "🔍", "识别文字", self._ocr)

        bar.addWidget(self._tb_sep())

        # 复制 / 保存 / 确认（绿）与取消（红）紧邻
        self._tb_btn(bar, "copy", "⧉", "复制", self._copy)
        self._tb_btn(bar, "save", "💾", "保存", self._save)
        self._tb_btn(bar, "confirm", "✓", "确认", self._confirm,
                     fg=TB_CONFIRM_FG)
        self._tb_btn(bar, "cancel", "✕", "取消", self._cancel,
                     fg=TB_CANCEL_FG)

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

    def _tb_btn(self, bar, key, text, tip, command, fg=None):
        b = QToolButton(self.tb_win)
        b.setText(text)
        b.setToolTip(tip)
        b.setFixedSize(28, 24)
        if fg is not None:
            b.setStyleSheet(f"QToolButton {{ color: {fg}; }}")
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
            self._color_click_timer.timeout.connect(self._toggle_color_row)
            self._color_click_timer.start()
        else:
            self._color_click_timer.stop()
            self._color_click_timer = None
            self._pick_color()

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
        screen_x = self.origin_x + tx
        screen_y = self.origin_y + ty
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
        sx = cx + self.origin_x
        sy = cy + self.origin_y
        return tx <= sx <= tx + tw and ty <= sy <= ty + th

    def _set_toolbar_enabled(self, enabled):
        """启用/禁用工具按钮。禁用时灰色 + 不响应。"""
        for b in self._tool_btns.values():
            b.setEnabled(enabled)
        self._update_tool_button_styles()

    def _update_tool_button_styles(self):
        """选中工具蓝色高亮，其余恢复正常色。"""
        for tool, b in self._tool_btns.items():
            if tool == self.tool:
                b.setStyleSheet(
                    f"QToolButton {{ color: {TB_ACTIVE}; font-weight: bold; }}")
                b.setProperty("active", True)
            else:
                b.setStyleSheet("")
                b.setProperty("active", False)

    # ------------------------------------------------------------------ #
    # 渲染（唯一入口：paintEvent；以下方法只更新状态并触发重绘）
    # ------------------------------------------------------------------ #

    def paintEvent(self, event):
        p = QPainter(self)
        W, H = self._actual_w, self._actual_h
        if self._base_pixmap is not None:
            p.drawPixmap(0, 0, self._base_pixmap)
        else:
            # 无定格截图（框选阶段 / 初始 select）：不透明黑底 + 窗口 0.5 透明度 → 桌面暗化
            p.fillRect(QRect(0, 0, W, H), QColor(0, 0, 0, 255))

        # adjust 模式统一在此渲染（含定格前的画框阶段：黑底上选区外暗化不可见，
        # 与旧版 stipple 黑叠黑等价，定格后底图透出即生效）；边框/尺寸标签全程可见
        if self._mode == "adjust" and self._region is not None:
            l, t, r, b = self._region
            if not self._masks_hidden:
                dim = QColor(0, 0, 0, MASK_ALPHA)
                p.fillRect(QRect(0, 0, W, t), dim)
                p.fillRect(QRect(0, t, l, b - t), dim)
                p.fillRect(QRect(r, t, W - r, b - t), dim)
                p.fillRect(QRect(0, b, W, H - b), dim)
            if self._region_pixmap is not None:
                p.drawPixmap(l, t, self._region_pixmap)
            self._paint_border(p, l, t, r, b)
            if not self._dragging_box:
                # 画框阶段不画手柄（与旧版轻量画框路径一致），定格完成后显示
                self._paint_handles(p, l, t, r, b)
        else:
            # select 模式（含已定格截图的场景）：整体暗化
            p.fillRect(QRect(0, 0, W, H), QColor(0, 0, 0, MASK_ALPHA))

        if self._crosshair is not None:
            self._paint_crosshair(p)
        if self._preview is not None:
            self._paint_preview(p)
        p.end()

    def _paint_border(self, p, l, t, r, b):
        """选区边框：白色外线 + 内侧深色细线（双线），白/深背景均可见。"""
        p.setPen(QPen(QColor(BORDER_OUTLINE), 2))
        p.drawRect(QRect(l, t, r - l, b - t))
        p.setPen(QPen(QColor(BORDER_INNER), 1))
        p.drawRect(QRect(l + 2, t + 2, (r - 2) - (l + 2), (b - 2) - (t + 2)))

    def _paint_handles(self, p, l, t, r, b):
        """8 个手柄：白底 + 深色描边（白色截图背景上手柄依然可见）。"""
        fill = QColor("#ffffff")
        pen = QPen(QColor(BORDER_INNER), 1)
        for hx, hy in self._handle_coords(l, t, r, b).values():
            s = self.HANDLE_SIZE
            p.fillRect(QRect(hx - s, hy - s, 2 * s, 2 * s), fill)
            p.setPen(pen)
            p.drawRect(QRect(hx - s, hy - s, 2 * s - 1, 2 * s - 1))

    def _paint_crosshair(self, p):
        x, y, w, h = self._crosshair
        W, H = self._actual_w, self._actual_h
        p.setPen(QPen(QColor(CROSSHAIR_COLOR), CROSSHAIR_WIDTH))
        p.drawLine(0, y, W, y)
        p.drawLine(x, 0, x, H)
        if w <= 0 or h <= 0:
            return
        text = f"{w} x {h}"
        font = QFont("Microsoft YaHei UI", 9)
        fm = QFontMetrics(font)
        tw, th = fm.horizontalAdvance(text), fm.height()
        lx, ly = x + 16, y + 16
        if lx + tw + 8 > W:
            lx = x - tw - 16
        if ly + th + 4 > H:
            ly = y - th - 16
        p.fillRect(QRect(lx - 4, ly - 2, tw + 8, th + 4), QColor(LABEL_BG))
        p.setPen(QPen(QColor(LABEL_FG)))
        p.drawText(QRect(lx - 4, ly - 2, tw + 8, th + 4),
                   Qt.AlignCenter, text)

    def _paint_preview(self, p):
        prev = self._preview
        color = QColor(self.color)
        if prev["tool"] == "arrow":
            (x0, y0), (x1, y1) = prev["start"], prev["end"]
            p.setPen(QPen(color, self.width_attr))
            p.drawLine(x0, y0, x1, y1)
            # 简易三角箭头头
            ang = math.atan2(y1 - y0, x1 - x0)
            hl = max(8.0, 2.5 * self.width_attr)
            for da in (-0.55, 0.55):
                p.drawLine(x1, y1,
                           int(x1 - hl * math.cos(ang + da)),
                           int(y1 - hl * math.sin(ang + da)))
            return
        px0, py0, px1, py1 = prev["box"]
        rect = QRectF(px0, py0, px1 - px0, py1 - py0)
        if prev["tool"] == "ellipse":
            p.setPen(QPen(color, self.width_attr))
            p.drawEllipse(rect)
        elif prev["tool"] == "mosaic":
            p.setPen(QPen(color, 1))
            p.drawRect(rect)
        else:  # rect
            p.setPen(QPen(color, self.width_attr))
            p.drawRect(rect)

    def _render_all(self):
        """同步派生状态（选区图 pixmap、工具条定位）并重绘。

        画框阶段与完整渲染共用同一条 paintEvent 路径：
        背景只贴一次（_base_pixmap 缓存），暗化/边框/手柄每帧按 _region
        状态重算，避免每帧创建整屏 PIL 位图。
        """
        if self._mode == "adjust" and self._region is not None:
            l, t, r, b = self._region
            if self._region_img is not None:
                self._refresh_region_pixmap()
            self._tb_show((l, t, r, b), (l, t, r - l, b - t))
        self.update()

    def _refresh_region_pixmap(self):
        """_region_img → 选区 QPixmap（标注/选区变化后调用）。"""
        if self._region_img is None:
            self._region_pixmap = None
            return
        try:
            self._region_pixmap = _pil_to_qpixmap(self._region_img)
        except Exception:
            self._region_pixmap = None

    def _render_moved_region(self):
        """移动选区（尺寸不变）：只移动取景框，不重裁、不重建遮罩。

        移动期间隐藏暗化遮罩、撤下选区图（_region_pixmap=None），
        由定格底图直接透出取景框内容，松开后重新 crop + 渲染。
        """
        l, t, r, b = self._region
        self._region_pixmap = None
        self._tb_show((l, t, r, b), (l, t, r - l, b - t))
        self.update()

    def _freeze_selection(self):
        """首次框选完成：隐藏窗口 → 捕获整屏 → 恢复窗口并定格画面。"""
        if self.full_img is not None or self.capture_screen is None:
            return
        self._tb_hide()
        self.hide()
        app = QApplication.instance()
        if app is not None:
            app.processEvents()  # 确保 hide 生效，避免把 overlay 截进画面
        try:
            full = self.capture_screen()
        finally:
            self.show()
            self.raise_()
        if full is None:
            raise RuntimeError("Screen capture returned no image")
        self.full_img = full
        self._base_pixmap = _pil_to_qpixmap(full)
        self._apply_window_opacity()

    # ------------------------------------------------------------------ #
    # 选区 crop
    # ------------------------------------------------------------------ #

    def _init_region_img(self):
        """从 full_img crop 出 region_img。"""
        if self.full_img is None:
            return
        l, t, r, b = self._region
        self._region_img = self.full_img.crop((l, t, r, b))

    def _adjust_region(self):
        """调整选区后重新 crop、清空标注和撤销栈。"""
        if self._region is None:
            # 手柄拖动到选区尺寸 < MIN_SIZE 时 _normalize 返回 None（_region 被置空）。
            # 此时选区无效，无法 crop / 清空标注，直接返回避免解包 None 崩溃。
            return
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

    # ------------------------------------------------------------------ #
    # 坐标 / 区域计算
    # ------------------------------------------------------------------ #

    def _to_virtual(self, wx, wy):
        return (wx + self.origin_x, wy + self.origin_y)

    def _calc_region(self, wx1, wy1, wx2, wy2):
        vx1, vy1 = self._to_virtual(wx1, wy1)
        vx2, vy2 = self._to_virtual(wx2, wy2)
        left = min(vx1, vx2)
        top = min(vy1, vy2)
        w = abs(vx2 - vx1)
        h = abs(vy2 - vy1)
        if w < self.MIN_SIZE or h < self.MIN_SIZE:
            return None
        return (left, top, w, h)

    def _to_virtual_region(self):
        """当前选区 → mss 虚拟桌面坐标 (left, top, width, height)；无选区返回 None。"""
        if self._region is None:
            return None
        l, t, r, b = self._region
        vx_l = int(l * self._scale_x)
        vx_t = int(t * self._scale_y)
        vx_r = int(r * self._scale_x)
        vx_b = int(b * self._scale_y)
        return (vx_l + self.origin_x, vx_t + self.origin_y, vx_r - vx_l, vx_b - vx_t)

    def _normalize(self, region):
        l, t, r, b = region
        l, r = min(l, r), max(l, r)
        t, b = min(t, b), max(t, b)
        W, H = self._actual_w, self._actual_h
        if r < 0 or b < 0 or l >= W or t >= H:
            return None
        l = max(0, min(l, W))
        t = max(0, min(t, H))
        r = max(0, min(r, W))
        b = max(0, min(b, H))
        if r - l < self.MIN_SIZE or b - t < self.MIN_SIZE:
            return None
        return (l, t, r, b)

    def _handle_coords(self, l, t, r, b):
        return {
            "nw": (l, t), "n": ((l + r) // 2, t), "ne": (r, t),
            "e": (r, (t + b) // 2), "se": (r, b),
            "s": ((l + r) // 2, b), "sw": (l, b), "w": (l, (t + b) // 2),
        }

    # ------------------------------------------------------------------ #
    # 鼠标事件
    # ------------------------------------------------------------------ #

    def _hit_test(self, x, y):
        if self._mode != "adjust" or self._region is None:
            return None
        # 画过标注后选区锁定：undo 栈非空表示已有标注，手柄不可拖动
        if self.undo:
            return None
        l, t, r, b = self._region
        s = self.HANDLE_SIZE // 2  # 6px 热区，避免标注误触手柄
        for name, (hx, hy) in self._handle_coords(l, t, r, b).items():
            if abs(x - hx) <= s and abs(y - hy) <= s:
                return name
        return None

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            self._cancel()
            return
        if event.button() != Qt.LeftButton:
            return
        x, y = _event_pos(event)
        # 点击工具条区域时忽略（工具条是独立窗口，正常不会收到这里的点击）
        if self._is_over_toolbar(x, y):
            return
        if self._mode == "select":
            # 按下立刻创建最小框并进入 adjust 模式
            self.start = (x, y)
            self._mode = "adjust"
            self._dragging_box = True
            W, H = self._actual_w, self._actual_h
            x = max(0, min(x, W - 1))
            y = max(0, min(y, H - 1))
            self._region = (x, y, x + 1, y + 1)
            self._init_region_img()
            self._set_toolbar_enabled(True)
            self._render_all()
            return
        if self._region is None:
            return
        hit = self._hit_test(x, y)
        if hit:
            self._drag_op = (hit, "resize")
            self._drag_start = (x, y)
            self._drag_region = self._region
            self._set_cursor_shape(self._CURSORS[hit])
            self._crosshair = None
            return
        l, t, r, b = self._region
        if self.tool == "move" and l <= x <= r and t <= y <= b:
            self._drag_op = ("move", "move")
            self._drag_start = (x, y)
            self._drag_region = self._region
            self._masks_hidden = True
            self._set_cursor_shape(self._CURSORS["move"])
            self._crosshair = None
            self.update()
            return
        # 选区内 → 开始标注
        if l <= x <= r and t <= y <= b:
            if self._region_img is None:
                return
            rx, ry = x - l, y - t
            if self.tool == "text":
                self._input_text(rx, ry)
                self.drawing = False
                self.draw_start = None
                return
            self.drawing = True
            self.draw_start = (rx, ry)
            self._brush_last = (rx, ry)
            self._crosshair = None
            self.undo.push(self._region_img.copy())
            if self.tool == "brush":
                d = ImageDraw.Draw(self._region_img)
                d.point(self.draw_start, fill=self.color)
                self._refresh_region_pixmap()
                self.update()
            self._update_cursor()

    def mouseMoveEvent(self, event):
        x, y = _event_pos(event)
        # select 模式：十字准星跟随
        if self._mode == "select":
            self._crosshair = (x, y, 0, 0)
            self._update_cursor()
            self.update()
            return
        if self._drag_op is None and self.start is not None:
            # select→adjust 过渡：更新框大小
            sx, sy = self.start
            x1, y1 = min(sx, x), min(sy, y)
            x2, y2 = max(sx, x), max(sy, y)
            W, H = self._actual_w, self._actual_h
            if sx <= 1 or x <= 1:
                x1 = 0
            if sy <= 1 or y <= 1:
                y1 = 0
            if sx >= W - 2 or x >= W - 2:
                x2 = W
            if sy >= H - 2 or y >= H - 2:
                y2 = H
            w = x2 - x1
            h = y2 - y1
            if w < self.MIN_SIZE and h < self.MIN_SIZE:
                return
            self._region = self._normalize((x1, y1, x2, y2))
            if self._region:
                l, t, r, b = self._region
                self._crosshair = (x, y, r - l, b - t)
            self._render_all()
            return
        # 手柄 resize / 移动
        if self._drag_op is not None and self._drag_start is not None:
            op, kind = self._drag_op
            dx = x - self._drag_start[0]
            dy = y - self._drag_start[1]
            l, t, r, b = self._drag_region
            if kind == "move":
                l, t, r, b = l + dx, t + dy, r + dx, b + dy
            else:
                if op in ("w", "sw", "nw"):
                    l = l + dx
                if op in ("e", "ne", "se"):
                    r = r + dx
                if op in ("n", "nw", "ne"):
                    t = t + dy
                if op in ("s", "sw", "se"):
                    b = b + dy
            previous_region = self._region
            self._region = self._normalize((l, t, r, b))
            if (
                kind == "move"
                and self.full_img is not None
                and self._region is not None
                and (self._region[2] - self._region[0],
                     self._region[3] - self._region[1])
                == (previous_region[2] - previous_region[0],
                    previous_region[3] - previous_region[1])
            ):
                self._render_moved_region()
            else:
                self._adjust_region()
            return
        # 标注
        if self.drawing and self._region_img is not None:
            l, t, r, b = self._region
            rx, ry = x - l, y - t
            rx = max(0, min(rx, r - l - 1))
            ry = max(0, min(ry, b - t - 1))
            if self.tool == "brush":
                draw_brush(self._region_img, [self._brush_last, (rx, ry)],
                           self.color, self.width_attr)
                self._brush_last = (rx, ry)
                self._refresh_region_pixmap()
                self.update()
            else:
                sx, sy = self.draw_start
                # 椭圆：按住 Alt 约束正圆预览
                if self.tool == "ellipse" and alt_pressed(event):
                    ex0, ey0, ex1, ey1 = self._constrain_ellipse(sx, sy, rx, ry)
                    self._set_preview(l + min(ex0, ex1), t + min(ey0, ey1),
                                      l + max(ex0, ex1), t + max(ey0, ey1))
                else:
                    self._set_preview(sx + l, sy + t, rx + l, ry + t)
        self._update_cursor(x, y)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        x, y = _event_pos(event)
        completed_move = (
            self._drag_op is not None and self._drag_op[1] == "move"
        )
        self.start = None
        self._drag_op = None
        self._drag_start = None
        self._drag_region = None

        self._update_cursor(x, y)
        self._crosshair = None
        # 画框结束：切换到完整渲染（定格 + 重裁选区图）
        if self._dragging_box:
            self._dragging_box = False
            if self._region is None:
                self._mode = "select"
                self._render_all()
                self.update()
                return
            self._freeze_selection()
            # _freeze_selection 里 hide → 截图 → show 会让 overlay 窗口重映射，
            # 键盘焦点可能随之丢失（落到之前的应用）。
            # 强制把焦点拉回 overlay，保证松开鼠标后直接可用键盘
            # （Ctrl+Z / Esc / Enter 等）操作选区。
            self._force_focus()
            self._init_region_img()
            self._render_all()
            self.update()
            return
        if self._region is None:
            self._mode = "select"
            self._render_all()
            self.update()
            return
        if completed_move and self.full_img is not None:
            self.undo.clear()
            self._init_region_img()
            self._masks_hidden = False
            self._render_all()
            self.update()
            return
        if not self.drawing or self._region_img is None:
            self._preview = None
            self.update()
            return
        self.drawing = False
        l, t, r, b = self._region
        rx, ry = x - l, y - t
        rx = max(0, min(rx, r - l - 1))
        ry = max(0, min(ry, b - t - 1))
        sx, sy = self.draw_start
        self._preview = None
        if self.tool == "brush":
            pass  # 画笔已在拖动中增量绘制
        elif self.tool == "rect":
            draw_rect(self._region_img,
                      [min(sx, rx), min(sy, ry), max(sx, rx), max(sy, ry)],
                      self.color, self.width_attr)
        elif self.tool == "ellipse":
            if abs(rx - sx) < 1 and abs(ry - sy) < 1:
                self._refresh_region_pixmap()
                self.update()
                return
            # 按住 Alt 约束为正圆
            ex0, ey0, ex1, ey1 = sx, sy, rx, ry
            if alt_pressed(event):
                ex0, ey0, ex1, ey1 = self._constrain_ellipse(sx, sy, rx, ry)
            draw_ellipse(self._region_img,
                         [min(ex0, ex1), min(ey0, ey1), max(ex0, ex1), max(ey0, ey1)],
                         self.color, self.width_attr)
        elif self.tool == "arrow":
            if abs(rx - sx) < 1 and abs(ry - sy) < 1:
                self._refresh_region_pixmap()
                self.update()
                return
            draw_arrow(self._region_img, (sx, sy), (rx, ry),
                       self.color, self.width_attr)
        elif self.tool == "mosaic":
            self._region_img = mosaic(
                self._region_img,
                min(sx, rx), min(sy, ry), max(sx, rx), max(sy, ry),
                block=24)
        self._refresh_region_pixmap()
        self.update()
        # 标注完成后确保工具条保持在最前（topmost 标志已含在窗口标志中，
        # raise_ 只调整置顶层内 Z 序，不抢键盘焦点）
        if self.tb_win is not None:
            self.tb_win.raise_()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._confirm()

    def _set_preview(self, x0, y0, x1, y1):
        """设置标注预览（绝对坐标），paintEvent 负责绘制。"""
        l, t, r, b = self._region
        px0 = max(l, min(x0, x1))
        py0 = max(t, min(y0, y1))
        px1 = min(r, max(x0, x1))
        py1 = min(b, max(y0, y1))
        if px1 - px0 < 1 or py1 - py0 < 1:
            self._preview = None
            self.update()
            return
        if self.tool == "arrow":
            # 箭头用实际起止点（不 clamp 到 bounding box），保证方向正确
            self._preview = {"tool": "arrow", "start": (x0, y0), "end": (x1, y1)}
        else:
            self._preview = {"tool": self.tool,
                             "box": (px0, py0, px1, py1)}
        self.update()

    def _update_cursor(self, x=None, y=None):
        if x is not None:
            if self._mode == "select" or self._region is None:
                cursor = Qt.CrossCursor
            else:
                hit = self._hit_test(x, y)
                if hit:
                    cursor = self._CURSORS[hit]
                else:
                    l, t, r, b = self._region
                    inside = l <= x <= r and t <= y <= b
                    cursor = (
                        self._CURSORS["move"]
                        if inside and self.tool == "move"
                        else Qt.CrossCursor
                    )
        else:
            cursor = Qt.CrossCursor
        self._set_cursor_shape(cursor)

    def _set_cursor_shape(self, shape):
        self._cursor = shape
        try:
            self.setCursor(shape)
        except Exception:
            pass

    def _constrain_ellipse(self, x0, y0, x1, y1):
        """按住 Alt 时约束椭圆为正圆：取横纵位移较大值作为边长。"""
        dx, dy = x1 - x0, y1 - y0
        d = max(abs(dx), abs(dy))
        sx = 1 if dx >= 0 else -1
        sy = 1 if dy >= 0 else -1
        return (x0, y0, x0 + sx * d, y0 + sy * d)

    # ------------------------------------------------------------------ #
    # 键盘
    # ------------------------------------------------------------------ #

    def keyPressEvent(self, event):
        k = event.key()
        if k == Qt.Key_Escape:
            self._cancel()
        elif k in (Qt.Key_Return, Qt.Key_Enter):
            self._confirm()
        elif k == Qt.Key_Z and (event.modifiers() & Qt.ControlModifier):
            self._undo()
        else:
            super().keyPressEvent(event)

    # ------------------------------------------------------------------ #
    # 文字输入
    # ------------------------------------------------------------------ #

    def _input_text(self, x, y):
        text = self._input_text_dialog(x, y)
        if text:
            self.undo.push(self._region_img.copy())
            draw_text(self._region_img, (x, y), text, self.color, 16)
            self._render_all()

    def _input_text_dialog(self, x, y):
        """模态文字输入对话框。确定（Enter / 按钮）返回文本，取消（Esc）返回 None。"""
        dlg = QDialog(self)
        dlg.setWindowTitle("输入文字")
        dlg.setWindowFlags(dlg.windowFlags() | Qt.WindowStaysOnTopHint)
        layout = QVBoxLayout(dlg)
        entry = QLineEdit(dlg)
        layout.addWidget(entry)
        btn = QPushButton("确定")
        btn.setDefault(True)
        btn.clicked.connect(dlg.accept)
        layout.addWidget(btn)
        dlg.resize(300, 80)
        entry.setFocus()
        if dlg.exec() == QDialog.Accepted:
            text = entry.text()
            return text if text else None
        return None

    # ------------------------------------------------------------------ #
    # 撤销 / 工具 / 颜色 / 粗细
    # ------------------------------------------------------------------ #

    def _undo(self):
        prev = self.undo.pop()
        if prev is not None:
            self._region_img = prev
            self._render_all()
            # 诊断日志：撤销后强制刷新，确认渲染生效。
            # 若用户报告"撤销没反应"，检查 _render_all 是否被调用、
            # _region_img 是否为预期的图。
            app = QApplication.instance()
            if app is not None:
                try:
                    app.processEvents()
                except Exception:
                    pass

    def _select_tool(self, name):
        self.tool = name
        self._prev_tool = name
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

    # ------------------------------------------------------------------ #
    # 识别文字（OCR）
    # ------------------------------------------------------------------ #

    def _ocr(self):
        """识别选区图中文字，弹窗显示，可复制到剪贴板。"""
        if self._region_img is None:
            return
        engine = get_ocr_engine()
        if engine is None:
            QMessageBox.critical(
                self, "识别失败",
                "未安装 OCR 引擎。\n请执行：pip install rapidocr\n"
                "（或：pip install rapidocr-onnxruntime）")
            return

        img = self._region_img.copy()

        def run():
            try:
                import numpy as np
                arr = np.array(img.convert("RGB"))
                result = engine(arr)
            except Exception as e:
                self._ocr_failed.emit(str(e))
                return
            self._ocr_text_ready.emit(ocr_result_to_text(result))

        # OCR 较慢，放到工作线程，避免卡住界面；
        # 结果通过跨线程信号排队回主线程处理。
        threading.Thread(target=run, daemon=True).start()

    def _on_ocr_failed(self, msg):
        if self._finished:
            return
        QMessageBox.critical(self, "识别失败", msg)

    def _show_ocr_result(self, text):
        """弹窗显示识别结果，支持复制全部 / 复制选中 / Ctrl+C。"""
        if self._finished:
            return
        if not text:
            QMessageBox.information(self, "识别文字", "未识别到文字。")
            return
        dlg = _OcrResultDialog(text, self)
        dlg.exec()

    # ------------------------------------------------------------------ #
    # 确认 / 取消 / 复制 / 保存
    # ------------------------------------------------------------------ #

    def _confirm(self):
        region = self._to_virtual_region()
        if region is None:
            self._cancel()
            return
        self.result = region
        self._finish(lambda: self.on_select(region, self._region_img))

    def _copy(self):
        region = self._to_virtual_region()
        if region is None:
            self._cancel()
            return
        self.result = region
        self._finish(lambda: self.on_copy(region, self._region_img))

    def _save(self):
        region = self._to_virtual_region()
        if region is None:
            self._cancel()
            return
        self.result = region
        self._finish(lambda: self.on_save(region, self._region_img))

    def _cancel(self):
        self.result = None
        self._finish(lambda: self.on_select(None, None))

    def _finish(self, callback):
        if self._finished:
            return
        self._finished = True
        self._pending_callback = callback
        self._tb_hide()
        self._crosshair = None
        self._preview = None
        self._exit()

    def _exit(self):
        """隐藏选区窗口并调用 pending 回调（幂等，可重复调用）。"""
        self.hide()
        cb = self._pending_callback
        self._pending_callback = None
        if cb is not None:
            cb()


# 向后兼容别名
RegionSelector = Selector
