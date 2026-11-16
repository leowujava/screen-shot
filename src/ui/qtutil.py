"""Qt 通用小工具：虚拟桌面尺寸 / PIL→QPixmap / 鼠标事件坐标。

从 selector.py 拆出的纯函数（不依赖 Selector）。selector 重导出这些名字
保持门面接口；注意 _virtual_screen_size / _pil_to_qpixmap 的调用点必须留在
selector.py（测试通过 monkeypatch selector 模块名字拦截/统计调用）。
"""
import ctypes
import sys

from PySide6.QtGui import QGuiApplication, QImage, QPixmap


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
