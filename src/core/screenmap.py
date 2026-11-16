"""虚拟桌面坐标映射：Qt 逻辑坐标 ↔ 整屏截图物理像素。

背景（Windows 高 DPI）：

- 选区 overlay 的窗口几何、鼠标事件使用 Qt 逻辑像素（DIP）；
- mss 截图在 per-monitor DPI aware 进程中是物理像素。

两者在 125% / 150% 等缩放下尺寸不同。本模块把整屏截图切成每个显示器
对应的物理矩形，并给出「逻辑局部矩形 → 物理矩形」映射：

    logical (lx, ly, lw, lh)  →  physical (px, py, pw, ph)

Selector 用它完成三件事：

1. 把物理整屏截图缩放到逻辑尺寸做显示底图；
2. 按选区逻辑坐标从物理截图中裁出原始分辨率图片；
3. 换算最终输出区域（mss 物理坐标）。

无法可靠匹配时（无 Qt、测试环境、非 Windows、显示器信息不一致）退化为
单屏均匀缩放映射，保证至少单屏高 DPI 下正确。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class ScreenMapping:
    """单个显示器的逻辑矩形与物理矩形（均以虚拟桌面左上角为原点）。"""

    lx: int
    ly: int
    lw: int
    lh: int
    px: int
    py: int
    pw: int
    ph: int
    sx: float
    sy: float

    def contains(self, x: float, y: float) -> bool:
        """逻辑局部坐标 (x, y) 是否落在该显示器内（含右/下边界）。"""
        return (self.lx <= x <= self.lx + self.lw
                and self.ly <= y <= self.ly + self.lh)

    def intersects(self, l: float, t: float, r: float, b: float) -> bool:
        """逻辑局部矩形是否与该显示器相交。"""
        return (self.lx < r and self.ly < b
                and self.lx + self.lw > l and self.ly + self.lh > t)

    def local_to_physical(self, x: float, y: float):
        """逻辑局部坐标 → 整屏截图物理坐标（浮点，调用方按需取整）。"""
        return (self.px + (x - self.lx) * self.sx,
                self.py + (y - self.ly) * self.sy)


def _win32_monitors():
    """Win32 物理显示器矩形 [(left, top, right, bottom), ...]；失败返回 []。

    进程为 per-monitor DPI aware 时（Qt 启动后会设置），这些坐标是物理像素。
    """
    if sys.platform != "win32":
        return []
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return []

    class _MonitorInfoEx(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
            ("szDevice", wintypes.WCHAR * 32),
        ]

    monitors = []
    enum_proc = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HANDLE, wintypes.HDC,
        ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

    def _callback(hmonitor, hdc, _rect, _lparam):
        info = _MonitorInfoEx()
        info.cbSize = ctypes.sizeof(_MonitorInfoEx)
        if ctypes.windll.user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            r = info.rcMonitor
            monitors.append((r.left, r.top, r.right, r.bottom))
        return True

    try:
        user32 = ctypes.windll.user32
        user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
        user32.GetMonitorInfoW.restype = wintypes.BOOL
        user32.EnumDisplayMonitors.argtypes = [
            wintypes.HDC, ctypes.POINTER(wintypes.RECT),
            enum_proc, wintypes.LPARAM]
        user32.EnumDisplayMonitors.restype = wintypes.BOOL
        user32.EnumDisplayMonitors(None, None, enum_proc(_callback), 0)
    except Exception:
        return []
    return monitors


def _qt_screens():
    """Qt 屏幕信息：((vx, vy, vw, vh), [((x, y, w, h), dpr), ...])。

    rect 为 Qt 全局逻辑坐标。无 QApplication / 查询失败返回 None。
    """
    try:
        from PySide6.QtGui import QGuiApplication
    except Exception:
        return None
    app = QGuiApplication.instance()
    if app is None:
        return None
    try:
        primary = app.primaryScreen()
        if primary is None:
            return None
        vg = primary.virtualGeometry()
        if vg.width() <= 0 or vg.height() <= 0:
            return None
        screens = []
        for screen in app.screens():
            g = screen.geometry()
            screens.append(
                ((g.x(), g.y(), g.width(), g.height()),
                 float(screen.devicePixelRatio())))
        return (vg.x(), vg.y(), vg.width(), vg.height()), screens
    except Exception:
        return None


def qt_virtual_origin():
    """Qt 虚拟桌面左上角的逻辑坐标；查询失败返回 None。"""
    info = _qt_screens()
    if info is None:
        return None
    vx, vy, _vw, _vh = info[0]
    return vx, vy


def _near(a, b, tolerance=2):
    return abs(a - b) <= tolerance


def _same_rect(a, b, tolerance=2):
    return all(_near(x, y, tolerance) for x, y in zip(a, b))


def _match_monitors(screens, monitors):
    """把每个 Qt 屏幕匹配到物理显示器矩形。

    先按「逻辑几何 × DPR ≈ 物理矩形」精确匹配（单屏/同 DPI 多屏）；
    失败时按「逻辑尺寸 × DPR ≈ 物理尺寸」匹配（应对 Qt 混合 DPI 下
    逻辑屏幕位置与物理位置不成比例的情况），同尺寸多屏按相对位置排序配对。

    返回与 screens 等长的物理矩形列表；无法可靠匹配返回 None。
    """
    remaining = list(monitors)
    matched = []
    for (gx, gy, gw, gh), dpr in screens:
        predicted = (round(gx * dpr), round(gy * dpr),
                     round((gx + gw) * dpr), round((gy + gh) * dpr))
        found = None
        for index, rect in enumerate(remaining):
            if _same_rect(predicted, rect):
                found = index
                break
        if found is None:
            return None
        matched.append(remaining.pop(found))
    return matched


def _match_monitors_by_size(screens, monitors):
    """按物理尺寸匹配 Qt 屏幕与显示器（位置不参与判定）。"""
    if len(screens) != len(monitors):
        return None

    result = [None] * len(screens)
    remaining = list(enumerate(monitors))

    # 第一轮：尺寸唯一匹配
    for index, ((gx, gy, gw, gh), dpr) in enumerate(screens):
        candidates = [
            (j, rect) for j, rect in remaining
            if _near(rect[2] - rect[0], gw * dpr)
            and _near(rect[3] - rect[1], gh * dpr)
        ]
        if len(candidates) == 1:
            j, rect = candidates[0]
            result[index] = rect
            remaining.remove((j, rect))

    # 第二轮：同尺寸多屏按相对位置（上→下、左→右）排序配对
    pending = [i for i, rect in enumerate(result) if rect is None]
    if len(pending) == len(remaining):
        pending.sort(key=lambda i: (screens[i][0][1], screens[i][0][0]))
        remaining.sort(key=lambda item: (item[1][1], item[1][0]))
        for index, (_j, rect) in zip(pending, remaining):
            ((gx, gy, gw, gh), dpr) = screens[index]
            if not (_near(rect[2] - rect[0], gw * dpr)
                    and _near(rect[3] - rect[1], gh * dpr)):
                return None
            result[index] = rect

    if any(rect is None for rect in result):
        return None
    return result



def build_mappings(logical_w, logical_h, physical_w, physical_h):
    """构建显示坐标映射。

    参数为虚拟桌面的逻辑尺寸（Qt）与物理尺寸（mss 截图）。

    返回 ``(mappings, exact)``：

    - ``mappings``：list[ScreenMapping]，逻辑/物理坐标均以虚拟桌面左上角为原点
    - ``exact``：True 表示按真实显示器逐屏映射；False 为单屏均匀缩放退化

    退化映射也能正确处理「单屏 + 任意缩放」：整张物理截图按比例缩放到底图，
    选区按比例从物理截图裁剪。多屏混合 DPI 只有在真实显示器信息一致时才会
    逐屏映射，否则退化为整屏均匀缩放（不会崩溃，只是无法逐屏最优）。
    """
    logical_w = int(logical_w or 0)
    logical_h = int(logical_h or 0)
    physical_w = int(physical_w or 0)
    physical_h = int(physical_h or 0)

    if logical_w > 0 and logical_h > 0 and physical_w > 0 and physical_h > 0:
        info = _qt_screens()
        monitors = _win32_monitors()
        if info is not None and monitors:
            (vx, vy, vw, vh), screens = info
            left = min(m[0] for m in monitors)
            top = min(m[1] for m in monitors)
            right = max(m[2] for m in monitors)
            bottom = max(m[3] for m in monitors)
            pw = right - left
            ph = bottom - top
            if (pw > 0 and ph > 0
                    and _near(pw, physical_w) and _near(ph, physical_h)
                    and _near(vw, logical_w) and _near(vh, logical_h)):
                matched = (_match_monitors(screens, monitors)
                           or _match_monitors_by_size(screens, monitors))
                if matched is not None:
                    entries = []
                    for ((gx, gy, gw, gh), _dpr), (pl, pt, pr, pb) in zip(
                            screens, matched):
                        if gw <= 0 or gh <= 0:
                            entries = None
                            break
                        pwid, phei = pr - pl, pb - pt
                        entries.append(ScreenMapping(
                            lx=gx - vx, ly=gy - vy, lw=gw, lh=gh,
                            px=pl - left, py=pt - top, pw=pwid, ph=phei,
                            sx=pwid / gw, sy=phei / gh,
                        ))
                    if entries:
                        return entries, True

    sx = physical_w / logical_w if logical_w else 1.0
    sy = physical_h / logical_h if logical_h else 1.0
    return [ScreenMapping(
        lx=0, ly=0, lw=logical_w, lh=logical_h,
        px=0, py=0, pw=physical_w, ph=physical_h,
        sx=sx, sy=sy,
    )], False
