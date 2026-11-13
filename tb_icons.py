"""工具条矢量图标（QPainter 线稿绘制 + 超采样缓存）。

从 selector.py 拆出：全部图标为单色线稿（圆头圆角），16% 安全边距，
任意 DPI 下清晰、风格一致。配色常量见 theme.py。
"""
import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)

from theme import PALETTE, TB_BG, TB_FG, TB_HOVER

# 工具条矢量图标绘制尺寸（逻辑像素）与超采样倍数
TB_ICON_SIZE = 20
TB_ICON_SCALE = 4

# 工具条图标缓存：(绘制函数, 颜色, 尺寸, 倍数) → QIcon。
# 工具条状态切换（选中/禁用）只换缓存，不重绘（每次切换 7 个 4× 超采样图标）。
_ICON_CACHE = {}


def _arrowhead(p, tx, ty, a_deg, size):
    """在 (tx, ty) 画指向 a_deg 方向的箭头两翼（翼向后展开 30°）。

    角度为屏幕坐标系（y 向下）：0°=右、90°=下、-90°=上、180°=左。
    """
    a = math.radians(a_deg)
    for da in (a + math.radians(150), a - math.radians(150)):
        p.drawLine(QPointF(tx, ty),
                   QPointF(tx + size * math.cos(da),
                           ty + size * math.sin(da)))


# --------------------------------------------------------------------------- #
# 各图标绘制函数：统一签名 (painter, s, c)，s=逻辑边长，c=线稿颜色（QColor）。
# 全部为单色线稿（圆头圆角），16% 安全边距，任意 DPI 下清晰、风格一致。
# --------------------------------------------------------------------------- #

def _paint_icon_move(p, s, c):
    """移动选区：四向箭头。"""
    pen = QPen(c, max(1.4, s * 0.08))
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    cx = cy = s * 0.5
    m = s * 0.18
    p.drawLine(QPointF(cx, m), QPointF(cx, s - m))
    p.drawLine(QPointF(m, cy), QPointF(s - m, cy))
    hz = s * 0.17
    _arrowhead(p, cx, m, -90, hz)    # 上
    _arrowhead(p, cx, s - m, 90, hz)    # 下
    _arrowhead(p, m, cy, 180, hz)    # 左
    _arrowhead(p, s - m, cy, 0, hz)   # 右


def _paint_icon_brush(p, s, c):
    """画笔：45° 铅笔 + 左下波浪线（钢笔带笔迹，参考设计样式）。"""
    pen = QPen(c, max(1.5, s * 0.08))
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    # 铅笔杆
    p.drawLine(QPointF(s * 0.44, s * 0.56), QPointF(s * 0.68, s * 0.32))
    # 笔尖（绘图端小实心三角）
    p.setPen(Qt.NoPen)
    p.setBrush(c)
    p.drawPolygon([QPointF(s * 0.34, s * 0.66),
                   QPointF(s * 0.45, s * 0.61),
                   QPointF(s * 0.39, s * 0.55)])
    # 笔尾箍（尾端短斜杠）
    pen2 = QPen(c, max(1.2, s * 0.06))
    pen2.setCapStyle(Qt.RoundCap)
    p.setPen(pen2)
    p.drawLine(QPointF(s * 0.62, s * 0.27), QPointF(s * 0.73, s * 0.38))
    # 波浪线（笔迹，三次贝塞尔 S 形）
    path = QPainterPath(QPointF(s * 0.24, s * 0.76))
    path.cubicTo(QPointF(s * 0.32, s * 0.66), QPointF(s * 0.42, s * 0.84),
                 QPointF(s * 0.54, s * 0.72))
    squig = QPen(c, max(1.3, s * 0.065))
    squig.setCapStyle(Qt.RoundCap)
    p.setPen(squig)
    p.drawPath(path)


def _paint_icon_rect(p, s, c):
    """矩形：圆角正方形（参考设计为方而非扁矩形）。"""
    pen = QPen(c, max(1.5, s * 0.08))
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(QRectF(s * 0.22, s * 0.22, s * 0.56, s * 0.56),
                      s * 0.06, s * 0.06)


def _paint_icon_ellipse(p, s, c):
    """圆形：正圆（参考设计为圆而非扁椭圆）。"""
    pen = QPen(c, max(1.5, s * 0.08))
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(QRectF(s * 0.22, s * 0.22, s * 0.56, s * 0.56))


def _paint_icon_arrow(p, s, c):
    """箭头：斜线杆 + 尖端直角开口箭头（Lucide arrow-up-right 风格）。"""
    pen = QPen(c, max(1.5, s * 0.08))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    # 斜向线杆
    p.drawLine(QPointF(s * 0.28, s * 0.72), QPointF(s * 0.64, s * 0.36))
    # 箭头两翼：水平 + 垂直，在尖端成直角开口
    p.drawLine(QPointF(s * 0.44, s * 0.28), QPointF(s * 0.72, s * 0.28))
    p.drawLine(QPointF(s * 0.72, s * 0.28), QPointF(s * 0.72, s * 0.56))


def _paint_icon_mosaic(p, s, c):
    """打码：圆角方框 + X 形 5 格填充（四角 + 中心，参考设计样式）。"""
    pen = QPen(c, max(1.4, s * 0.07))
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(QRectF(s * 0.20, s * 0.20, s * 0.60, s * 0.60),
                      s * 0.06, s * 0.06)
    p.setPen(Qt.NoPen)
    p.setBrush(c)
    for cy, cx in ((0.30, 0.30), (0.30, 0.70), (0.50, 0.50),
                   (0.70, 0.30), (0.70, 0.70)):
        half = s * 0.055
        p.drawRect(QRectF(s * cx - half, s * cy - half, 2 * half, 2 * half))


def _paint_icon_text(p, s, c):
    """文字：衬线 T（Lucide type 风格：顶横 + 两侧衬线 + 底部衬线）。"""
    pen = QPen(c, max(1.5, s * 0.08))
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawLine(QPointF(s * 0.26, s * 0.28), QPointF(s * 0.74, s * 0.28))  # 顶横
    p.drawLine(QPointF(s * 0.26, s * 0.28), QPointF(s * 0.26, s * 0.38))  # 左衬线
    p.drawLine(QPointF(s * 0.74, s * 0.28), QPointF(s * 0.74, s * 0.38))  # 右衬线
    p.drawLine(QPointF(s * 0.50, s * 0.28), QPointF(s * 0.50, s * 0.72))  # 竖杆
    p.drawLine(QPointF(s * 0.38, s * 0.72), QPointF(s * 0.62, s * 0.72))  # 底衬线


def _arc_pts(cx, cy, r, a0, a1, steps=48):
    """圆弧折线点序列（屏幕坐标，y 向下；a0→a1 按角度增减方向绘制）。"""
    pts = []
    for i in range(steps + 1):
        a = math.radians(a0 + (a1 - a0) * i / steps)
        pts.append(QPointF(cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def _paint_icon_undo(p, s, c):
    """撤销：钩状回转箭头（左指箭头 + C 形钩，参考设计 ↩ 样式）。"""
    pen = QPen(c, max(1.5, s * 0.08))
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    # 左指箭头（尖端在左上）
    _arrowhead(p, s * 0.26, s * 0.38, 180, s * 0.14)
    # 箭杆 + 钩形弧（从顶部起顺时针 240°，末端朝左下）
    p.drawLine(QPointF(s * 0.30, s * 0.38), QPointF(s * 0.52, s * 0.38))
    p.drawPolyline(_arc_pts(s * 0.52, s * 0.54, s * 0.16, 270, 510))


def _paint_icon_copy(p, s, c):
    """复制：两个叠放的圆角方形。"""
    pen = QPen(c, max(1.4, s * 0.07))
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    # 后方方形
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(QRectF(s * 0.34, s * 0.20, s * 0.44, s * 0.44),
                      s * 0.05, s * 0.05)
    # 前方方形（底色遮盖 + 描边）
    p.setBrush(QColor(TB_BG))
    p.drawRoundedRect(QRectF(s * 0.22, s * 0.36, s * 0.44, s * 0.44),
                      s * 0.05, s * 0.05)


def _paint_icon_ocr(p, s, c):
    """识别文字：扫描文本样式（四个 L 形角框 + 中央 3 条文本行，参考设计样式）。"""
    pen = QPen(c, max(1.4, s * 0.07))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    # 四角框（L 形）
    p.drawPolyline([QPointF(s * 0.24, s * 0.40), QPointF(s * 0.24, s * 0.24),
                    QPointF(s * 0.40, s * 0.24)])
    p.drawPolyline([QPointF(s * 0.60, s * 0.24), QPointF(s * 0.76, s * 0.24),
                    QPointF(s * 0.76, s * 0.40)])
    p.drawPolyline([QPointF(s * 0.76, s * 0.60), QPointF(s * 0.76, s * 0.76),
                    QPointF(s * 0.60, s * 0.76)])
    p.drawPolyline([QPointF(s * 0.40, s * 0.76), QPointF(s * 0.24, s * 0.76),
                    QPointF(s * 0.24, s * 0.60)])
    # 中央文本行（两长一短）
    p.drawLine(QPointF(s * 0.36, s * 0.40), QPointF(s * 0.64, s * 0.40))
    p.drawLine(QPointF(s * 0.36, s * 0.50), QPointF(s * 0.64, s * 0.50))
    p.drawLine(QPointF(s * 0.36, s * 0.60), QPointF(s * 0.54, s * 0.60))


def _paint_icon_color(p, s, c):
    """选择颜色：调色板主体 + 拇指孔 + 取自真实预设色板的前 3 色色点。"""
    # 调色板主体（浅灰底 + 线稿色描边）
    p.setPen(QPen(c, max(1.2, s * 0.055)))
    p.setBrush(QColor(TB_HOVER))
    p.drawEllipse(QRectF(s * 0.09, s * 0.14, s * 0.82, s * 0.68))
    # 拇指孔
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(TB_BG))
    p.drawEllipse(QPointF(s * 0.62, s * 0.56), s * 0.115, s * 0.115)
    # 色点（与预设色板前 3 色一致，不随状态变色）
    dot_r = s * 0.088
    for (dx, dy), col in zip(
        ((s * 0.30, s * 0.36), (s * 0.50, s * 0.27), (s * 0.70, s * 0.36)),
        (QColor(pc) for pc in PALETTE[:3]),
    ):
        p.setBrush(col)
        p.drawEllipse(QPointF(dx, dy), dot_r, dot_r)


def _paint_icon_save(p, s, c):
    """保存：下载样式（向下箭头 + 底部托盘，参考设计样式，替代软盘）。"""
    pen = QPen(c, max(1.5, s * 0.08))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    # 垂直箭杆 + 向下箭头
    p.drawLine(QPointF(s * 0.50, s * 0.18), QPointF(s * 0.50, s * 0.52))
    _arrowhead(p, s * 0.50, s * 0.62, 90, s * 0.13)
    # 底部托盘（U 形，开口向上）
    p.drawPolyline([QPointF(s * 0.30, s * 0.66), QPointF(s * 0.30, s * 0.78),
                    QPointF(s * 0.70, s * 0.78), QPointF(s * 0.70, s * 0.66)])


def _paint_icon_confirm(p, s, c):
    """确认：对勾（细线条，参考设计样式）。"""
    pen = QPen(c, max(1.6, s * 0.09))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.drawPolyline([QPointF(s * 0.28, s * 0.52),
                    QPointF(s * 0.42, s * 0.66),
                    QPointF(s * 0.72, s * 0.32)])


def _paint_icon_cancel(p, s, c):
    """取消：叉号（细线条，参考设计样式）。"""
    pen = QPen(c, max(1.6, s * 0.09))
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.drawLine(QPointF(s * 0.32, s * 0.32), QPointF(s * 0.68, s * 0.68))
    p.drawLine(QPointF(s * 0.68, s * 0.32), QPointF(s * 0.32, s * 0.68))


# 标注工具 → 图标绘制函数（与 Selector.TOOLS 一一对应）
TOOL_ICON_FNS = {
    "move": _paint_icon_move,
    "brush": _paint_icon_brush,
    "rect": _paint_icon_rect,
    "ellipse": _paint_icon_ellipse,
    "arrow": _paint_icon_arrow,
    "mosaic": _paint_icon_mosaic,
    "text": _paint_icon_text,
}


def _tb_icon(paint_fn, color=TB_FG, target=TB_ICON_SIZE, scale=TB_ICON_SCALE):
    """矢量绘制工具条图标：高倍超采样 + devicePixelRatio，任意 DPI 下都清晰。

    paint_fn 签名 (painter, s, c)；color 可传颜色字符串或 QColor。
    按 (paint_fn, color, target, scale) 缓存 QIcon，同一图标不重复渲染
    （工具条状态切换只取缓存，见 _ICON_CACHE）。
    """
    if not isinstance(color, QColor):
        color = QColor(color)
    key = (paint_fn, color.name(), target, scale)
    icon = _ICON_CACHE.get(key)
    if icon is not None:
        return icon
    px = target * scale
    pm = QPixmap(px, px)
    pm.setDevicePixelRatio(scale)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    paint_fn(p, target, color)
    p.end()
    icon = QIcon(pm)
    _ICON_CACHE[key] = icon
    return icon
