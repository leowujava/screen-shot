"""选区 + 标注（PySide6 实现）。

合并选区 + 标注：拖拽框选 → 完整工具条 → 画完框直接标注 → 确认/复制/保存/取消。

模块拆分：配色常量见 theme.py，工具条矢量图标见 tb_icons.py，
工具条窗口 / OCR 弹窗见 widgets.py；本文件只保留选区状态机与渲染。

窗口模型：
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
import sys
import threading

from PIL import Image, ImageChops, ImageDraw

from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, QSize, Qt, Signal, QTimer
from PySide6.QtGui import (
    QBrush,
    QColor,
    QPolygonF,
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
    QToolButton,
    QVBoxLayout,
    QWidget,
    QMessageBox,
)

from applog import get_logger
from drawing import (
    arrow_geometry, draw_arrow, draw_rect, draw_ellipse, draw_brush,
    draw_text, mosaic, UndoStack, get_ocr_engine, ocr_result_to_text,
    alt_pressed,
)
from screenmap import build_mappings, qt_virtual_origin

from theme import (
    BORDER_INNER, BORDER_OUTLINE, CROSSHAIR_COLOR, CROSSHAIR_WIDTH,
    DIM_WINDOW_OPACITY, FULL_WINDOW_OPACITY, LABEL_BG, LABEL_FG,
    MASK_ALPHA, PALETTE, TB_ACTIVE, TB_BG, TB_CANCEL_FG, TB_CONFIRM_FG,
    TB_DISABLED, TB_FG, TB_HOVER, TB_SEP,
)
# 图标与辅助窗口拆到独立模块；这里再导出全部名字，
# 保持 `import selector` 的既有接口（测试/手动脚本直接引用）。
from tb_icons import (
    TB_ICON_SCALE, TB_ICON_SIZE, TOOL_ICON_FNS, _tb_icon,
    _paint_icon_arrow, _paint_icon_brush, _paint_icon_cancel,
    _paint_icon_color, _paint_icon_confirm, _paint_icon_copy,
    _paint_icon_ellipse, _paint_icon_mosaic, _paint_icon_move,
    _paint_icon_ocr, _paint_icon_rect, _paint_icon_save,
    _paint_icon_text, _paint_icon_undo,
)
from widgets import _OcrResultDialog, _Toolbar

# 标注层二值化 LUT（0 → 0，其余 → 255）。
# .point() 传 LUT 走 C 级查表，替代逐像素 Python lambda
# （选区移动重定基时对整张选区图执行，lambda 版百万像素级耗时）。
_THRESHOLD_LUT = (0,) + (255,) * 255

# 撤销快照内存预算：每份快照 ≈ 选区宽 × 高 × 3 字节（RGB），
# 快照总数按选区尺寸动态计算（见 Selector._undo_maxlen_for）。
# 固定 50 份对小选区足够"撤销回干净底图"，但 4K 选区（≈24.9MB/份）
# 50 份会逼近 1.3GB；按预算折算后 4K ≈ 10 份、1080p ≈ 43 份，
# 撤销内存始终有界，小选区体验不变。
_UNDO_MEMORY_BUDGET = 256 * 1024 * 1024
_UNDO_MAXLEN = 50
_UNDO_MINLEN = 5


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
    """PIL Image → QPixmap（不拷贝 img，转换一次缓存使用）。

    不透明图（定格截图/选区图均为 RGB）直接输出 BGRX 字节串 +
    Format_RGB32：省去一次整图 convert("RGBA") 拷贝 + 一次整图通道
    转换（约 选区尺寸 × 4 字节），标注/撤销/换色/画笔整图补齐等
    路径的每次转换都更便宜。Format_RGB32 为 Windows 原生格式，
    上传不做字节交换，且 Qt 按不透明处理，与旧 RGBA8888 渲染一致。
    """
    if img.mode != "RGB":
        img = img.convert("RGB")
    data = img.tobytes("raw", "BGRX")
    # QImage 不拷贝 data 缓冲，但 fromImage 立即深拷贝为 QPixmap，
    # 返回时像素已归 Qt 所有——不要改成返回 QImage（会悬垂）。
    return QPixmap.fromImage(QImage(data, img.width, img.height,
                                    QImage.Format_RGB32))


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
    capture_screen: 可选的整屏截图函数（无参，返回 PIL Image），
                    full_img 为 None 时框选完成后调用。
    """

    MIN_SIZE = 5
    HANDLE_SIZE = 12
    MARGIN = 8

    # 画笔拖动中每 N 段整图补齐一次：防止长笔画上累计 patch 包围盒无限增长
    # （patch 覆盖"上次整图转换以来"的全部线段，不能只靠单段小 patch，
    # 否则拖动中只剩最后一段可见、"看不见轨迹"）。
    _BRUSH_FULL_REFRESH_EVERY = 12

    # 8 个手柄的标识
    HANDLES = ("nw", "n", "ne", "e", "se", "s", "sw", "w")

    # 各手柄的窗口光标（拖动方向提示）
    _CURSORS = {"nw": Qt.SizeFDiagCursor, "n": Qt.SizeVerCursor,
                "ne": Qt.SizeBDiagCursor, "e": Qt.SizeHorCursor,
                "se": Qt.SizeFDiagCursor, "s": Qt.SizeVerCursor,
                "sw": Qt.SizeBDiagCursor, "w": Qt.SizeHorCursor,
                "move": Qt.SizeAllCursor}

    # 标注工具（取色已集成到颜色面板，不在主工具条）。
    # 顺序对齐参考设计：move 之后 矩形→圆→箭头→画笔→文字→马赛克。
    TOOLS = ("move", "rect", "ellipse", "arrow", "brush", "text", "mosaic")
    # 图标为矢量绘制（见 tb_icons.TOOL_ICON_FNS / _tb_icon），不用文本/emoji
    TOOL_LABELS = {"move": "移动选区", "brush": "画笔", "rect": "矩形", "ellipse": "圆形",
                   "arrow": "箭头", "mosaic": "打码", "text": "文字"}

    # OCR 线程 → 主线程（跨线程信号自动排队投递到主线程）
    _ocr_text_ready = Signal(str)
    _ocr_failed = Signal(str)

    def __init__(self, x, y, width, height, full_img, on_select,
                 on_copy, on_save, capture_screen=None):
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
        self._brush_patch = None  # 画笔 stroke 中的局部 patch：(QPixmap, ox, oy)，选区局部坐标
        self._brush_bbox = None   # 自上次整图转换以来全部线段点的累计包围盒（选区局部坐标）
        self._brush_moves = 0     # 自上次整图转换以来的 move 段数（周期补齐计数）
        self._label_fm = None     # 十字准星尺寸标签 QFontMetrics（惰性缓存，避免每帧重建）
        # 选区图物理分辨率 / 逻辑分辨率之比（高 DPI 下 > 1；普通屏为 1）
        self._region_scale = 1.0
        self._draw_start_logical = None
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
        self._ocr_busy = False  # OCR 进行中：忽略连点，避免叠开多个结果弹窗

        # 标注状态（默认颜色 = 色板第一色，与截图中箭头红色一致）
        self.tool = "move"
        self.color = PALETTE[0]
        self.width_attr = 3
        # 撤销深度上限：每笔标注都保存一份选区图副本。上限过低（如 10）时，
        # 画满上限后最旧快照被挤出，撤销到栈空也回不到干净底图（"撤销没干净"）。
        # 初始 50 笔；选区 crop 后按尺寸动态调整（_init_region_img →
        # _undo_maxlen_for），大选区自动降份数，总快照内存不超过 _UNDO_MEMORY_BUDGET。
        self.undo = UndoStack(maxlen=_UNDO_MAXLEN)
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

        # 动态计算实际屏幕大小（虚拟桌面，Qt 逻辑像素）
        self._actual_w, self._actual_h = _virtual_screen_size()
        if self._actual_w <= 0 or self._actual_h <= 0:
            # 查询失败（如测试环境）：退回调用方传入的尺寸
            self._actual_w, self._actual_h = width, height
        # 全局缩放比：mss 物理像素 / Qt 逻辑像素（单屏高 DPI 下即 DPR）
        self._scale_x = width / self._actual_w if self._actual_w else 1.0
        self._scale_y = height / self._actual_h if self._actual_h else 1.0
        self._region_scale = self._scale_x
        # 逐显示器映射：逻辑局部矩形 → 整屏截图物理矩形。
        # 真实显示器信息一致时精确到每块屏（混合 DPI）；否则退化为整屏均匀缩放。
        self._screens, self._mapping_exact = build_mappings(
            self._actual_w, self._actual_h, width, height)
        # 窗口几何使用 Qt 逻辑坐标；origin_x/y 仍是截图物理坐标（输出用）。
        # Qt 可用时以它的虚拟桌面逻辑原点定位窗口/工具条（高 DPI、多屏都正确）；
        # 查询失败（纯逻辑测试）才回退到调用方传入的坐标。
        self._logical_origin_x, self._logical_origin_y = (
            qt_virtual_origin() or (x, y))
        self.setGeometry(self._logical_origin_x, self._logical_origin_y,
                         self._actual_w, self._actual_h)

        if self.full_img is not None:
            self._base_pixmap = self._build_base_pixmap(self.full_img)
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

    def _build_base_pixmap(self, img):
        """整屏物理截图 → 逻辑尺寸的显示底图。

        高 DPI 下 Qt 窗口是逻辑像素、mss 截图是物理像素，直接
        ``drawPixmap(0, 0)`` 会让底图被裁掉右下部分（并导致选区内容错位）。
        这里按显示器把物理截图缩放/拼接到逻辑尺寸：

        - 普通屏（尺寸一致）：直接转换，行为与旧版完全一致；
        - 单屏高 DPI：整图缩放到逻辑尺寸；
        - 多屏混合 DPI：逐屏裁剪并缩放到各自的逻辑矩形。

        注意：Qt 在混合 DPI 下各屏的逻辑矩形可能重叠，单个窗口只能按
        一份逻辑画布显示，重叠区最终显示其中一块屏的内容；选区输出走
        `_crop_region` 的逐屏物理映射，不受这一显示限制影响。
        """
        W, H = self._actual_w, self._actual_h
        if img.size == (W, H) or W <= 0 or H <= 0:
            return _pil_to_qpixmap(img)

        display = Image.new("RGB", (W, H))
        iw, ih = img.size
        for m in sorted(self._screens, key=lambda m: max(m.sx, m.sy)):
            # 逻辑矩形裁剪到窗口范围
            lx = max(0, m.lx)
            ly = max(0, m.ly)
            rx = min(W, m.lx + m.lw)
            ry = min(H, m.ly + m.lh)
            if rx <= lx or ry <= ly:
                continue
            # 对应物理矩形（到整屏截图像素），按逻辑裁剪量反算
            fx0, fy0 = m.local_to_physical(lx, ly)
            fx1, fy1 = m.local_to_physical(rx, ry)
            box = (max(0, min(int(round(fx0)), iw)),
                   max(0, min(int(round(fy0)), ih)),
                   max(0, min(int(round(fx1)), iw)),
                   max(0, min(int(round(fy1)), ih)))
            if box[2] <= box[0] or box[3] <= box[1]:
                continue
            crop = img.crop(box)
            if crop.size != (rx - lx, ry - ly):
                crop = crop.resize((rx - lx, ry - ly), Image.LANCZOS)
            display.paste(crop, (lx, ly))
        return _pil_to_qpixmap(display)

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

    # ------------------------------------------------------------------ #
    # 渲染（唯一入口：paintEvent；以下方法只更新状态并触发重绘）
    # ------------------------------------------------------------------ #

    def paintEvent(self, event):
        p = QPainter(self)
        try:
            self._paint_all(p)
        finally:
            # 绘制中途抛异常也必须 end，否则 QPainter 残留 active 状态
            p.end()

    def _paint_all(self, p):
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
            # 画笔 stroke 中：局部线段 patch 叠在选区底图 pixmap 上
            #（避免每帧整图 PIL→QPixmap 转换；收笔后整图转换并清 patch）
            if self._brush_patch is not None:
                pm, ox, oy = self._brush_patch
                s = self._region_scale
                p.drawPixmap(QPointF(l + ox / s, t + oy / s), pm)
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

    def _label_font_metrics(self):
        """十字准星尺寸标签的 QFontMetrics（惰性缓存）。

        QFontMetrics 构造涉及字体布局计算，框选过程十字准星每帧都会用，
        缓存后避免每帧重建。
        """
        if self._label_fm is None:
            self._label_fm = QFontMetrics(QFont("Microsoft YaHei UI", 9))
        return self._label_fm

    def _paint_crosshair(self, p):
        x, y, w, h = self._crosshair
        W, H = self._actual_w, self._actual_h
        p.setPen(QPen(QColor(CROSSHAIR_COLOR), CROSSHAIR_WIDTH))
        p.drawLine(0, y, W, y)
        p.drawLine(x, 0, x, H)
        if w <= 0 or h <= 0:
            return
        text = f"{w} x {h}"
        fm = self._label_font_metrics()
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
            # 与最终渲染（draw_arrow）共用同一几何：锥形杆 + 实心三角
            shaft, head_pts = arrow_geometry((x0, y0), (x1, y1), self.width_attr)
            if shaft is None:  # 太短：画一条短线
                p.setPen(QPen(color, self.width_attr))
                p.drawLine(x0, y0, x1, y1)
                return
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(color))
            p.drawPolygon(QPolygonF([QPointF(x, y) for x, y in shaft]))
            p.drawPolygon(QPolygonF([QPointF(x, y) for x, y in head_pts]))
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
        """_region_img → 选区 QPixmap（标注/选区变化后调用）。

        整图转换结果取代任何旧局部 patch（_brush_patch 置空）。
        """
        self._brush_patch = None
        if self._region_img is None:
            self._region_pixmap = None
            return
        try:
            self._region_pixmap = _pil_to_qpixmap(self._region_img)
            # 物理分辨率的选区图按 DPR 缩放绘制到逻辑选区矩形：
            # 普通屏 DPR=1 行为不变；高 DPI 下显示尺寸正确且保留物理细节。
            self._region_pixmap.setDevicePixelRatio(self._region_scale)
        except Exception:
            self._region_pixmap = None

    def _expand_brush_bbox(self, p0, p1):
        """把线段两端点并入累计包围盒并返回 (x0, y0, x1, y1)（选区局部坐标）。

        累计包围盒覆盖"自上次整图转换以来"的全部线段点：只覆盖最后一段会
        导致拖动中早期轨迹落在 patch 之外（表现为"看不见轨迹"）。
        """
        b = self._brush_bbox
        if b is None:
            b = (min(p0[0], p1[0]), min(p0[1], p1[1]),
                 max(p0[0], p1[0]), max(p0[1], p1[1]))
        else:
            b = (min(b[0], p0[0], p1[0]), min(b[1], p0[1], p1[1]),
                 max(b[2], p0[0], p1[0]), max(b[3], p0[1], p1[1]))
        self._brush_bbox = b
        return b

    def _refresh_brush_patch(self, x0, y0, x1, y1):
        """画笔 stroke 中：只把累计线段包围盒转成 patch QPixmap，不做整图转换。

        (x0, y0, x1, y1) 为自上次整图转换以来全部线段点的包围盒（选区局部
        坐标）。外扩边距与 draw_brush 的局部超采样边距一致（max(2×笔宽, 8)），
        保证覆盖线段全部像素。paintEvent 把 patch 合成在选区底图 pixmap 上；
        每 _BRUSH_FULL_REFRESH_EVERY 段与收笔后各整图转换一次并清掉 patch。
        """
        if self._region_img is None:
            self._brush_patch = None
            return
        margin = max(2 * self.width_attr * self._region_scale, 8)
        W, H = self._region_img.size
        cx0, cy0 = max(0, int(x0) - margin), max(0, int(y0) - margin)
        cx1, cy1 = min(W, int(x1) + margin), min(H, int(y1) + margin)
        if cx1 - cx0 < 1 or cy1 - cy0 < 1:
            # 包围盒退化（极短线段）：退回整图转换，避免预览缺失
            self._brush_patch = None
            self._refresh_region_pixmap()
            return
        try:
            pm = _pil_to_qpixmap(self._region_img.crop((cx0, cy0, cx1, cy1)))
            pm.setDevicePixelRatio(self._region_scale)
        except Exception:
            pm = None
        self._brush_patch = (pm, cx0, cy0) if pm is not None else None

    def _render_moved_region(self):
        """轻量渲染：不重裁、不重建选区 pixmap，由定格底图直接透出取景框内容。

        用于两类每帧调用的拖动路径（选区内容此刻必然等于定格底图 crop）：
        - 移动选区（尺寸不变）：移动期间另隐藏暗化遮罩（_masks_hidden）；
        - 手柄缩放 / 贴边裁剪（_adjust_region）：undo 已清空、无标注。
        落笔标注或收笔后会重新整图转换 _region_pixmap。
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
        self._base_pixmap = self._build_base_pixmap(full)
        self._apply_window_opacity()

    def _abort_capture(self, exc):
        """截屏/裁剪失败兜底：回到 select 模式并提示，异常不穿透 Qt 事件循环。"""
        get_logger().error("截图失败", exc_info=True)
        self.full_img = None
        self._base_pixmap = None
        self._region_img = None
        self._region_pixmap = None
        self._brush_patch = None
        self._brush_bbox = None
        self.undo.clear()
        self._region = None
        self._mode = "select"
        self._dragging_box = False
        self._draw_start_logical = None
        self._crosshair = None
        self._preview = None
        self._tb_hide()
        self._set_toolbar_enabled(False)
        self._apply_window_opacity()
        self.update()
        try:
            QMessageBox.critical(self, "截图失败", str(exc))
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # 选区 crop
    # ------------------------------------------------------------------ #

    def _region_scale_for(self, region):
        """选区覆盖屏幕中最大的物理/逻辑缩放比（输出分辨率不降级）。

        单屏高 DPI：等于该屏 DPR；普通屏：1.0。选区跨多屏混合 DPI 时取最大
        值，保证输出至少和最高 DPI 屏幕的物理像素一一对应。
        """
        l, t, r, b = region
        scales = [max(m.sx, m.sy) for m in self._screens
                  if m.intersects(l, t, r, b)]
        if scales:
            return max(scales)
        return self._scale_x or 1.0

    def _region_img_size(self, region):
        """当前缩放比下，选区图应有的物理像素尺寸。"""
        s = self._region_scale
        l, t, r, b = region
        return (max(1, int(round((r - l) * s))),
                max(1, int(round((b - t) * s))))

    def _crop_region(self, region, scale):
        """按逻辑选区从整屏物理截图裁出选区图（物理分辨率）。

        普通屏走与原实现等价的单次 crop；高 DPI / 多屏时逐显示器裁剪后
        缩放到 ``逻辑尺寸 × scale`` 并拼接，保证输出为原始物理分辨率。
        """
        if self.full_img is None:
            return None
        l, t, r, b = region
        ow = max(1, int(round((r - l) * scale)))
        oh = max(1, int(round((b - t) * scale)))
        out = Image.new("RGB", (ow, oh))
        iw, ih = self.full_img.size
        # 混合 DPI 下逻辑屏幕矩形可能重叠：按缩放比从低到高粘贴，
        # 让高分辨率屏幕的物理内容覆盖重叠区（与 _local_to_full 一致）。
        for m in sorted(self._screens, key=lambda m: max(m.sx, m.sy)):
            il = max(l, m.lx)
            it = max(t, m.ly)
            ir = min(r, m.lx + m.lw)
            ib = min(b, m.ly + m.lh)
            if ir <= il or ib <= it:
                continue
            fx0, fy0 = m.local_to_physical(il, it)
            fx1, fy1 = m.local_to_physical(ir, ib)
            box = (max(0, min(int(round(fx0)), iw)),
                   max(0, min(int(round(fy0)), ih)),
                   max(0, min(int(round(fx1)), iw)),
                   max(0, min(int(round(fy1)), ih)))
            if box[2] <= box[0] or box[3] <= box[1]:
                continue
            crop = self.full_img.crop(box)
            tw = max(1, int(round((ir - il) * scale)))
            th = max(1, int(round((ib - it) * scale)))
            if crop.size != (tw, th):
                crop = crop.resize((tw, th), Image.LANCZOS)
            out.paste(crop, (int(round((il - l) * scale)),
                             int(round((it - t) * scale))))
        return out

    def _init_region_img(self):
        """从 full_img crop 出 region_img，并按选区尺寸调整撤销快照上限。"""
        if self.full_img is None:
            return
        scale = self._region_scale_for(self._region)
        img = self._crop_region(self._region, scale)
        if img is None:
            return
        self._region_scale = scale
        self._region_img = img
        self.undo.maxlen = self._undo_maxlen_for(img.width, img.height)

    def _region_point(self, x, y):
        """overlay 逻辑坐标 → 选区图物理坐标（越界自动 clamp）。

        普通屏 scale=1 时与旧实现 ``x - left`` 完全等价。
        """
        l, t, r, b = self._region
        s = self._region_scale
        rx = int(round((x - l) * s))
        ry = int(round((y - t) * s))
        max_x = max(0, int(round((r - l) * s)) - 1)
        max_y = max(0, int(round((b - t) * s)) - 1)
        return max(0, min(rx, max_x)), max(0, min(ry, max_y))

    @staticmethod
    def _undo_maxlen_for(w, h):
        """撤销快照份数上限：min(_UNDO_MAXLEN, 预算 // 单份字节数)，下限 _UNDO_MINLEN。"""
        per = max(1, w * h * 3)
        return max(_UNDO_MINLEN, min(_UNDO_MAXLEN, _UNDO_MEMORY_BUDGET // per))

    def _rebase_annotations(self, img, old_base, new_base):
        """选区移动（尺寸不变）：把 img 的标注层重新定基到新位置。

        标注层 = img 与旧位置底图 old_base 的像素差（含抗锯齿边缘）；
        贴到新位置底图 new_base 上——新位置的屏幕内容可见，标注跟随保留。
        无标注（纯白/原图）时返回 new_base 的副本（仅位置变化）。
        """
        if img.size != new_base.size:
            return img
        # C 级 LUT 二值化（任意非零差 → 255），替代逐像素 Python lambda
        mask = ImageChops.difference(img, old_base).convert("L").point(
            _THRESHOLD_LUT)
        if mask.getbbox() is None:
            return new_base.copy()
        base = new_base.copy()
        base.paste(img, (0, 0), mask)
        return base

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
        # 缩放/贴边裁剪后 undo 已清空 → 选区内容必然等于定格底图 crop：
        # 走底图透出的轻量渲染（与移动路径同），避免手柄拖动缩放时
        # 每帧整图 PIL→QPixmap 转换（resize 帧开销主因）。
        self._render_moved_region()

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

    @staticmethod
    def _is_tiny_region(region):
        """region 是否仍是按下时的 1x1 占位框（纯点击、未拖出真实选区）。

        框选过渡中 mouseMoveEvent 只在实际宽或高 ≥ MIN_SIZE 时才更新选区，
        因此选区仍 ≤1px 即代表用户只是点了一下。
        """
        l, t, r, b = region
        return (r - l) <= 1 and (b - t) <= 1

    def _local_to_full(self, x, y):
        """overlay 逻辑坐标 → 整屏截图物理坐标（full_img 像素）。

        精确逐屏映射时按所在显示器换算（混合 DPI 也正确）；逻辑屏幕矩形
        可能重叠（Qt 混合 DPI），重叠区取缩放比最大的屏幕（与选区裁剪时
        高分辨率块覆盖低分辨率块的规则一致）。否则按全局缩放比退化。
        """
        best = None
        for m in self._screens:
            if m.contains(x, y):
                if best is None or max(m.sx, m.sy) >= max(best.sx, best.sy):
                    best = m
        if best is not None:
            fx, fy = best.local_to_physical(x, y)
            return int(round(fx)), int(round(fy))
        return int(round(x * self._scale_x)), int(round(y * self._scale_y))

    def _to_virtual_region(self):
        """当前选区 → mss 虚拟桌面坐标 (left, top, width, height)；无选区返回 None。"""
        if self._region is None:
            return None
        l, t, r, b = self._region
        if self._mapping_exact:
            fx_l, fy_t = self._local_to_full(l, t)
            fx_r, fy_b = self._local_to_full(r, b)
        else:
            fx_l, fy_t = int(l * self._scale_x), int(t * self._scale_y)
            fx_r, fy_b = int(r * self._scale_x), int(b * self._scale_y)
        return (fx_l + self.origin_x, fy_t + self.origin_y,
                fx_r - fx_l, fy_b - fy_t)

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
            rx, ry = self._region_point(x, y)
            if self.tool == "text":
                self._input_text(rx, ry)
                self.drawing = False
                self.draw_start = None
                self._draw_start_logical = None
                return
            self.drawing = True
            self.draw_start = (rx, ry)
            self._draw_start_logical = (x, y)
            self._brush_last = (rx, ry)
            self._crosshair = None
            self.undo.push(self._region_img.copy())
            if self.tool == "brush":
                d = ImageDraw.Draw(self._region_img)
                d.point(self.draw_start, fill=self.color)
                # 起笔整图转换一次作底图，并重置累计 patch 状态
                # （bbox=None：首帧由 _expand_brush_bbox 从 _brush_last/当前点初始化）
                self._brush_moves = 0
                self._brush_bbox = None
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
                # 标签显示实际输出（物理）尺寸，高 DPI 下与保存结果一致
                scale = self._region_scale_for(self._region)
                self._crosshair = (
                    x, y,
                    int(round((r - l) * scale)),
                    int(round((b - t) * scale)),
                )
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
            rx, ry = self._region_point(x, y)
            s = self._region_scale
            if self.tool == "brush":
                draw_brush(self._region_img, [self._brush_last, (rx, ry)],
                           self.color, self.width_attr * s)
                # 局部 patch 增量更新：只转换"自上次整图转换以来"累计线段
                # 包围盒，避免每帧整图转换，也不能只转最后一段（会丢轨迹）。
                # 每 _BRUSH_FULL_REFRESH_EVERY 段整图补齐一次，防止包围盒膨胀。
                self._brush_moves += 1
                if self._brush_moves >= self._BRUSH_FULL_REFRESH_EVERY:
                    self._brush_moves = 0
                    self._brush_bbox = None
                    self._refresh_region_pixmap()
                else:
                    x0, y0, x1, y1 = self._expand_brush_bbox(
                        self._brush_last, (rx, ry))
                    self._refresh_brush_patch(x0, y0, x1, y1)
                self._brush_last = (rx, ry)
                self.update()
            else:
                # 预览始终用 overlay 逻辑坐标（QPainter 直接画在窗口上）
                sx, sy = self._draw_start_logical or (x, y)
                if self.tool == "ellipse" and alt_pressed(event):
                    ex0, ey0, ex1, ey1 = self._constrain_ellipse(sx, sy, x, y)
                    self._set_preview(min(ex0, ex1), min(ey0, ey1),
                                      max(ex0, ex1), max(ey0, ey1))
                else:
                    self._set_preview(sx, sy, x, y)
        self._update_cursor(x, y)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        x, y = _event_pos(event)
        completed_move = (
            self._drag_op is not None and self._drag_op[1] == "move"
        )
        moved_from = self._drag_region  # 拖动开始时的选区（移动重定基用）
        self.start = None
        self._drag_op = None
        self._drag_start = None
        self._drag_region = None

        self._update_cursor(x, y)
        self._crosshair = None
        # 画框结束：切换到完整渲染（定格 + 重裁选区图）
        if self._dragging_box:
            self._dragging_box = False
            if self._region is None or self._is_tiny_region(self._region):
                # 纯点击（未拖出 ≥ MIN_SIZE 的框，选区仍是按下时的 1x1 占位框）：
                # 回到 select 模式且不定格——一次点击不应触发 hide→截屏→show
                # 的整屏捕获与闪烁，也不应留下 1x1 选区（与 MIN_SIZE 语义一致）。
                self._region = None
                self._mode = "select"
                self._set_toolbar_enabled(False)
                self._tb_hide()
                self._render_all()
                self.update()
                return
            try:
                self._freeze_selection()
                # _freeze_selection 里 hide → 截图 → show 会让 overlay 窗口重映射，
                # 键盘焦点可能随之丢失（落到之前的应用）。
                # 强制把焦点拉回 overlay，保证松开鼠标后直接可用键盘
                # （Ctrl+Z / Esc / Enter 等）操作选区。
                self._force_focus()
                self._init_region_img()
                self._render_all()
            except Exception as exc:
                # 锁屏 / RDP 断开 / 显示器变化等导致截屏失败时，异常若穿透
                # Qt 事件循环会直接结束进程，这里统一回退到 select 模式。
                self._abort_capture(exc)
                return
            self.update()
            return
        if self._region is None:
            # 手柄拖到选区 < MIN_SIZE（_normalize 置空）后落笔：回到 select 模式，
            # 收起工具条（此前工具条会残留悬浮在 select 模式的画面上）。
            self._mode = "select"
            self._set_toolbar_enabled(False)
            self._tb_hide()
            self._render_all()
            self.update()
            return
        if completed_move and self.full_img is not None:
            # 移动保持尺寸：标注跟随内容——把标注层（旧图与旧位置底图的差）贴到
            # 新位置底图上；撤销快照同样重新定基，Ctrl+Z 依然有效。
            l, t, r, b = self._region
            old_img = self._region_img
            old_region = moved_from
            if (old_img is not None and old_region is not None
                    and old_img.size == self._region_img_size(self._region)):
                old_base = self._crop_region(old_region, self._region_scale)
                if old_base.size == old_img.size:
                    new_base = self._crop_region(self._region, self._region_scale)
                    self._region_img = self._rebase_annotations(
                        old_img, old_base, new_base)
                    self.undo.map(
                        lambda im: self._rebase_annotations(
                            im, old_base, new_base))
                else:
                    self._init_region_img()
            else:
                # 尺寸变了（贴边被裁剪；移动中 _adjust_region 已重裁并清空标注）
                self._init_region_img()
            self._masks_hidden = False
            self._render_all()
            self.update()
            return
        if not self.drawing or self._region_img is None:
            self._preview = None
            self._draw_start_logical = None
            self.update()
            return
        self.drawing = False
        rx, ry = self._region_point(x, y)
        sx, sy = self.draw_start
        self._draw_start_logical = None
        s = self._region_scale
        self._preview = None
        if self.tool == "brush":
            # 画笔已在拖动中增量绘制；重置累计 patch 状态（下方统一整图转换）
            self._brush_moves = 0
            self._brush_bbox = None
        elif self.tool == "rect":
            draw_rect(self._region_img,
                      [min(sx, rx), min(sy, ry), max(sx, rx), max(sy, ry)],
                      self.color, self.width_attr * s)
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
                         self.color, self.width_attr * s)
        elif self.tool == "arrow":
            if abs(rx - sx) < 1 and abs(ry - sy) < 1:
                self._refresh_region_pixmap()
                self.update()
                return
            draw_arrow(self._region_img, (sx, sy), (rx, ry),
                       self.color, self.width_attr * s)
        elif self.tool == "mosaic":
            self._region_img = mosaic(
                self._region_img,
                min(sx, rx), min(sy, ry), max(sx, rx), max(sy, ry),
                block=24 * s)
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
            # 端点 clamp 到选区（与最终渲染的 _region_point 一致），
            # 预览不画出选区外；分别 clamp 两端，方向保持正确
            ax0, ay0 = max(l, min(x0, r)), max(t, min(y0, b))
            ax1, ay1 = max(l, min(x1, r)), max(t, min(y1, b))
            self._preview = {"tool": "arrow", "start": (ax0, ay0), "end": (ax1, ay1)}
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
            # 字号随线宽旋钮联动（12 + 2×线宽），与其他工具行为一致
            font_size = max(1, int(round((12 + 2 * self.width_attr)
                                         * self._region_scale)))
            draw_text(self._region_img, (x, y), text, self.color, font_size)
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
            # update() 已触发重绘；不要在此 processEvents——嵌套事件循环
            # 会让排队的 OCR 弹窗/定时器在撤销中途重入。
            self._render_all()

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

    # ------------------------------------------------------------------ #
    # 识别文字（OCR）
    # ------------------------------------------------------------------ #

    def _ocr(self):
        """识别选区图中文字，弹窗显示，可复制到剪贴板。

        引擎懒加载（首次会读 OCR 模型）也放在工作线程：否则第一次点击
        "识别文字"会在主线程阻塞数秒，与"识别期间界面不卡死"不符。
        进行中禁用按钮并忽略连点：避免叠开多个线程/结果弹窗。
        """
        if self._region_img is None or self._ocr_busy:
            return
        self._ocr_busy = True
        self._tb_btns["ocr"].setEnabled(False)

        img = self._region_img.copy()

        def run():
            try:
                engine = get_ocr_engine()
                if engine is None:
                    self._ocr_failed.emit(
                        "未安装 OCR 引擎。\n请执行：pip install rapidocr\n"
                        "（或：pip install rapidocr-onnxruntime）")
                    return
                import numpy as np
                result = engine(np.array(img.convert("RGB")))
            except Exception as e:
                self._ocr_failed.emit(str(e))
                return
            self._ocr_text_ready.emit(ocr_result_to_text(result))

        # OCR 较慢，放到工作线程，避免卡住界面；
        # 结果通过跨线程信号排队回主线程处理。
        threading.Thread(target=run, daemon=True).start()

    def _ocr_done(self):
        """OCR 结束（成功/失败共用）：复位进行中状态，恢复按钮。"""
        self._ocr_busy = False
        self._tb_btns["ocr"].setEnabled(True)

    def _on_ocr_failed(self, msg):
        self._ocr_done()
        if self._finished:
            return
        QMessageBox.critical(self, "识别失败", msg)

    def _show_ocr_result(self, text):
        """弹窗显示识别结果，支持复制全部 / 复制选中 / Ctrl+C。"""
        self._ocr_done()
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
        # 回调完成后释放大块内存（定格截图 / 底图 pixmap / 选区图 / 撤销快照）。
        # 窗口仍被 app._selector_holder 引用，不释放则多屏定格截图（数十 MB）
        # 会滞留到下一次框选。注意必须排在回调之后：回调 lambda 调用时
        # 读取 self._region_img。
        self.full_img = None
        self._base_pixmap = None
        self._region_img = None
        self._region_pixmap = None
        self._brush_patch = None
        self._brush_bbox = None
        self.undo.clear()
