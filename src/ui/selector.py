"""选区 + 标注（PySide6 实现）。

合并选区 + 标注：拖拽框选 → 完整工具条 → 画完框直接标注 → 确认/复制/保存/取消。

模块拆分：配色常量见 theme.py，工具条矢量图标见 tb_icons.py，
工具条窗口 / OCR 弹窗见 widgets.py，Qt 通用小工具见 qtutil.py；
ToolbarMixin（selector_toolbar.py）/ MouseMixin（selector_mouse.py）/
RegionMixin（selector_region.py）通过多重继承组合进 Selector。
本文件保留选区状态机核心：初始化 / 窗口 / 渲染 / 键盘 / 文字输入 /
撤销 / OCR / 确认动作。子模块均不反向依赖 selector；本文件重导出
拆出的名字，保持 `import selector` 的既有接口（测试/手动脚本直接引用）。

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
import threading

from PIL import Image

from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QPolygonF,
    QFont,
    QFontMetrics,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.applog import get_logger
from ..core.config import get_config
from ..core.drawing import (
    UndoStack, arrow_geometry, draw_text, get_ocr_engine, ocr_result_to_text,
)
from ..core.screenmap import build_mappings, qt_virtual_origin

from .theme import (
    BORDER_INNER, BORDER_OUTLINE, CROSSHAIR_COLOR, CROSSHAIR_WIDTH,
    DIM_WINDOW_OPACITY, FULL_WINDOW_OPACITY, LABEL_BG, LABEL_FG,
    MASK_ALPHA, PALETTE,
)
# 图标与辅助窗口拆到独立模块；这里再导出全部名字，
# 保持 `from src.ui import selector` 的既有接口（测试/手动脚本直接引用）。
from .tb_icons import (
    TB_ICON_SCALE, TB_ICON_SIZE, TOOL_ICON_FNS, _tb_icon,
    _paint_icon_arrow, _paint_icon_brush, _paint_icon_cancel,
    _paint_icon_color, _paint_icon_confirm, _paint_icon_copy,
    _paint_icon_ellipse, _paint_icon_mosaic, _paint_icon_move,
    _paint_icon_ocr, _paint_icon_rect, _paint_icon_save,
    _paint_icon_text, _paint_icon_undo,
)
from .widgets import _OcrResultDialog, _Toolbar
# Qt 通用工具 / 选区几何 / 撤销预算常量拆到独立模块；重导出保持门面接口。
# 注意：_virtual_screen_size / _pil_to_qpixmap 的调用点必须留在本文件
# （测试 monkeypatch selector 模块名字来拦截/统计调用）。
from .qtutil import _event_pos, _pil_to_qpixmap, _virtual_screen_size
from .selector_region import (
    RegionMixin, _THRESHOLD_LUT, _UNDO_MEMORY_BUDGET, _UNDO_MAXLEN,
    _UNDO_MINLEN,
)
from .selector_toolbar import ToolbarMixin
from .selector_mouse import MouseMixin


class Selector(ToolbarMixin, MouseMixin, RegionMixin, QWidget):
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

        # 标注状态（默认颜色/线宽来自用户配置，缺省 = 色板第一色红色 / 3）
        self.tool = "move"
        cfg = get_config()
        self.color = cfg["color"]
        self.width_attr = cfg["width"]
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
    # 撤销
    # ------------------------------------------------------------------ #

    def _undo(self):
        prev = self.undo.pop()
        if prev is not None:
            self._region_img = prev
            # update() 已触发重绘；不要在此 processEvents——嵌套事件循环
            # 会让排队的 OCR 弹窗/定时器在撤销中途重入。
            self._render_all()
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
