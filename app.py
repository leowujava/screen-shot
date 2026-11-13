import ctypes
import os
import sys
import threading
from PIL import Image, ImageDraw
import pynput.keyboard as kb
import pystray

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import QApplication, QMessageBox

from capture import (
    capture_fullscreen,
    capture_virtual_screen_size,
    copy_to_clipboard,
    save_image,
)
from selector import Selector

# 全局持有：Qt 应用、主线程调度器、未结束的选区窗口
# （选区窗口由 C++ 端引用计数管理，Python 对象被 GC 时窗口会提前销毁）
_gui = {}
_selector_holder = []


class _GuiDispatcher(QObject):
    """把 fn 调度到 Qt 主线程执行（替代旧 Tk root.after(0, fn)）。

    - 主线程调用：直接执行（本应用的 GUI 调度都来自子线程，主线程直调仅
      覆盖测试等无事件循环的场景）。
    - 子线程调用：通过跨线程信号排队投递到主线程事件循环。
    - 无 QApplication（如纯逻辑单测）：直接执行。
    """

    _sig = Signal(object)

    def __init__(self):
        super().__init__()
        self._sig.connect(self._run)

    def schedule(self, fn):
        app = QApplication.instance()
        if app is None:
            fn()
            return
        if QThread.currentThread() == app.thread():
            fn()
            return
        self._sig.emit(fn)

    def _run(self, fn):
        fn()


def _run_on_main(fn):
    """在 Qt 主线程上执行 fn。

    从快捷键（pynput 监听线程）或托盘（pystray 线程）调用 GUI 操作时，
    必须调度回主线程（Qt 要求所有窗口/控件操作在 GUI 线程进行）。
    """
    d = _gui.get("dispatcher")
    if d is None:
        fn()
        return
    d.schedule(fn)


def process_fullscreen_capture(img):
    """全屏截图 → 保存并复制到剪贴板，返回路径。"""
    path = save_image(img, None)
    _safe_copy_to_clipboard(img)
    return path


def _safe_copy_to_clipboard(img):
    """复制到剪贴板；失败时弹出提示而不是静默吞掉。"""
    try:
        copy_to_clipboard(img)
    except Exception as e:
        import traceback
        traceback.print_exc()

        def _show_error():
            try:
                QMessageBox.critical(None, "复制失败", str(e))
            except Exception:
                pass

        _run_on_main(_show_error)


def process_region_selection(region, annotated_img):
    """选区 + 标注结果 → 复制到剪贴板。region 为 None 时不做任何事。

    确认后不再打开 Editor，只在 Selector 中完成标注后复制到剪贴板（不自动保存）。
    region: (left, top, width, height) 虚拟桌面坐标
    annotated_img: PIL Image（选区 crop + 标注）
    必须在主线程（Qt 事件循环所在线程）调用。
    """
    if region is None or annotated_img is None:
        return
    _safe_copy_to_clipboard(annotated_img)


def _virtual_screen_origin():
    """返回虚拟桌面左上角 (x, y)。"""
    user32 = ctypes.windll.user32
    return (user32.GetSystemMetrics(76), user32.GetSystemMetrics(77))


def _on_copy(region, annotated_img):
    """选区"复制"回调：region 为 None（取消）时不做任何事。"""
    if region is None:
        return
    _safe_copy_to_clipboard(annotated_img)


def _on_save(region, annotated_img):
    """选区"保存"回调：region 为 None（取消）时不做任何事。"""
    if region is None:
        return
    save_image(annotated_img, None)


def _has_active_selector():
    """是否存在未结束的选区窗口（防快捷键/托盘连点叠开多个 overlay）。"""
    return any(not getattr(old, "_finished", True) for old in _selector_holder)


def open_region_selector(vx, vy, vsize):
    """创建并显示覆盖整个虚拟桌面的选区窗口（必须在主线程调用）。

    已有未结束选区时忽略本次触发（不清理、不新建），避免叠窗；
    已结束的历史选区窗口则清理掉，释放定格截图等内存。
    """
    if _has_active_selector():
        return
    for old in list(_selector_holder):
        if getattr(old, "_finished", False):
            _selector_holder.remove(old)
    sel = Selector(
        x=vx, y=vy, width=vsize[0], height=vsize[1],
        full_img=None,
        capture_screen=capture_fullscreen,
        on_select=process_region_selection,
        on_copy=_on_copy,
        on_save=_on_save,
    )
    _selector_holder.append(sel)
    sel.show()


def do_region_capture():
    """框选区域截图（供快捷键和托盘调用）。

    先在工作线程获取桌面尺寸，再把选区窗口调度回主线程创建
    （Qt 要求所有窗口操作在 GUI 线程进行）。
    画出初始选区后才隐藏窗口并抓取屏幕，避免唤出时过早固定画面。
    """
    def worker():
        vsize = capture_virtual_screen_size()
        vx, vy = _virtual_screen_origin()
        _run_on_main(lambda: open_region_selector(vx, vy, vsize))

    threading.Thread(target=worker, daemon=True).start()


def do_fullscreen_capture():
    """全屏截图直接保存（并复制到剪贴板）。耗时操作在工作线程执行。"""
    def worker():
        full = capture_fullscreen()
        process_fullscreen_capture(full)
    threading.Thread(target=worker, daemon=True).start()


def do_copy_capture():
    """全屏截图只复制到剪贴板，不保存。耗时操作在工作线程执行。"""
    def worker():
        full = capture_fullscreen()
        _safe_copy_to_clipboard(full)
    threading.Thread(target=worker, daemon=True).start()


def _make_tray_icon_image():
    """生成托盘图标（相机外形）。"""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle([8, 20, 56, 52], outline="white", width=3)
    d.ellipse([22, 28, 42, 48], outline="white", width=3)
    d.rectangle([24, 14, 40, 20], fill="white")
    return img


def main():
    """主入口。

    - 主线程：QApplication 事件循环（Windows 消息泵）+ pynput 快捷键监听。
      pynput 监听线程自带消息循环，主线程只需有事件循环存活。
    - 子线程：pystray 托盘（阻塞）、耗时的截图/保存/剪贴板。
    - 所有 Qt 窗口（选区等）都在主线程创建、由 Qt 事件循环驱动；
      子线程需要触达 GUI 时通过 _run_on_main 调度回主线程。
    """
    app = QApplication(sys.argv)
    # 选区窗口 hide 后不应导致应用退出（本应用没有常驻主窗口）
    app.setQuitOnLastWindowClosed(False)
    _gui["app"] = app
    _gui["dispatcher"] = _GuiDispatcher()

    tray_holder = {}

    def on_quit():
        """退出：停止托盘 → 退出 Qt 事件循环 → 强制结束进程。

        on_quit 可能从托盘线程或 pynput 线程调用；app.quit() 调度回主线程
        才能退出事件循环。随后 main 收尾处的 os._exit 兜底，避免
        onnxruntime 等非 daemon 线程让进程卡住。
        """
        if "icon" in tray_holder:
            try:
                tray_holder["icon"].stop()
            except Exception:
                pass
        # 调度回主线程退出事件循环
        _run_on_main(app.quit)

    def run_tray():
        icon = pystray.Icon(
            "screen-shot",
            _make_tray_icon_image(),
            "截图工具",
            pystray.Menu(
                # default=True：左键点击图标直接触发框选截图（右键菜单中该项照常保留）
                pystray.MenuItem("框选截图", lambda i, m: do_region_capture(), default=True),
                pystray.MenuItem("全屏截图", lambda i, m: do_fullscreen_capture()),
                pystray.MenuItem("复制截图", lambda i, m: do_copy_capture()),
                pystray.MenuItem("退出", lambda i, m: on_quit()),
            ),
        )
        tray_holder["icon"] = icon
        icon.run()

    t = threading.Thread(target=run_tray, daemon=True)
    t.start()

    with kb.GlobalHotKeys({
        "<ctrl>+<shift>+x": lambda: do_region_capture(),
        "<ctrl>+<shift>+s": lambda: do_fullscreen_capture(),
        "<ctrl>+<shift>+q": lambda: on_quit(),
    }):
        try:
            app.exec()
        except KeyboardInterrupt:
            pass
        # 清理：停止托盘
        if "icon" in tray_holder:
            try:
                tray_holder["icon"].stop()
            except Exception:
                pass
        os._exit(0)


if __name__ == "__main__":
    main()
