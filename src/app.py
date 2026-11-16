import ctypes
import os
import sys
import threading
from PIL import Image, ImageDraw
import pynput.keyboard as kb
import pystray

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from .core.applog import get_logger
from .core.capture import (
    capture_fullscreen,
    capture_virtual_screen_size,
    copy_to_clipboard,
    default_save_path,
    save_image,
    save_image_as,
)
from .core.config import DEFAULT_HOTKEYS, get_config
from .ui.selector import Selector

# 全局持有：Qt 应用、主线程调度器、未结束的选区窗口
# （选区窗口由 C++ 端引用计数管理，Python 对象被 GC 时窗口会提前销毁）
_gui = {}
_selector_holder = []


class _GuiDispatcher(QObject):
    """把 fn 调度到 Qt 主线程执行。

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


def _report_error(title, exc):
    """记录错误（文件日志 + 控制台）并在主线程弹出提示（不静默吞掉；调用方在哪个线程都能安全用）。"""
    get_logger().error("%s: %s", title, exc, exc_info=True)

    def _show_error():
        try:
            QMessageBox.critical(None, title, str(exc))
        except Exception:
            pass

    _run_on_main(_show_error)


def process_fullscreen_capture(img, path=None):
    """全屏截图 → 保存并复制到剪贴板，返回保存路径（保存失败时为 None）。

    path 为用户在"另存为"对话框选择的路径（写确切路径，覆盖已由对话框确认）；
    为 None 时走 save_image 自动命名（配置 save_dir 或桌面 + 时间戳，同名自动后缀）。
    保存失败只弹提示、不阻断后续剪贴板复制（用户仍可 Ctrl+V 粘贴），不静默吞掉。
    """
    saved = None
    try:
        saved = save_image_as(img, path) if path else save_image(img, None)
    except Exception as e:
        _report_error("保存失败", e)
    _safe_copy_to_clipboard(img)
    return saved


def _safe_copy_to_clipboard(img):
    """复制到剪贴板；失败时弹出提示而不是静默吞掉。"""
    try:
        copy_to_clipboard(img)
    except Exception as e:
        _report_error("复制失败", e)


def _safe_save_as(img, path):
    """保存截图到用户指定路径；失败时弹出提示而不是崩溃或静默吞掉。"""
    try:
        save_image_as(img, path)
    except Exception as e:
        _report_error("保存失败", e)


# 上一次"另存为"选中的目录：下次对话框默认打开这里（文件名仍按时间戳生成）
_last_save_dir = None


def _ask_save_path():
    """弹"另存为"对话框选择保存路径；取消返回 None。必须在主线程调用。

    默认目录为上一次选中的目录（首次取配置 save_dir 或桌面），
    默认文件名 截图_时间戳.png。无 QApplication（纯逻辑单测）时
    不弹对话框，直接返回默认路径。
    """
    global _last_save_dir
    default = default_save_path()
    if _last_save_dir:
        default = os.path.join(_last_save_dir, os.path.basename(default))
    if QApplication.instance() is None:
        return default
    path, _ = QFileDialog.getSaveFileName(None, "保存截图", default, "PNG 图片 (*.png)")
    if not path:
        return None
    _last_save_dir = os.path.dirname(path)
    return path


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
    """选区"保存"回调：region 为 None（取消）时不做任何事。

    先在主线程弹"另存为"对话框选择路径（取消则不保存）；PNG 编码是
    CPU 密集操作：放到工作线程执行，避免大选区（数秒级）卡住主线程
    事件循环；失败弹提示（_safe_save_as），不再让异常沿
    _exit → _finish 传播到 Qt 事件循环导致整个应用崩溃。
    """
    if region is None:
        return
    path = _ask_save_path()
    if path is None:
        return

    def worker():
        _safe_save_as(annotated_img, path)

    threading.Thread(target=worker, daemon=True).start()


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
    """全屏截图：弹"另存为"对话框选择保存路径后保存（并复制到剪贴板）。

    截屏在工作线程执行；对话框调度回主线程弹出（Qt 要求所有窗口操作
    在 GUI 线程进行）；编码保存再回工作线程。取消对话框则不保存，
    但仍复制到剪贴板。
    """
    def worker():
        full = capture_fullscreen()
        _run_on_main(lambda: _show_fullscreen_save_dialog(full))

    threading.Thread(target=worker, daemon=True).start()


def _show_fullscreen_save_dialog(full):
    """主线程：弹"另存为"对话框，随后在工作线程保存 + 复制（取消 → 只复制）。"""
    path = _ask_save_path()

    def worker():
        if path is None:
            _safe_copy_to_clipboard(full)
        else:
            process_fullscreen_capture(full, path)

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


_SINGLE_INSTANCE_MUTEX = "Local\\screen-shot-single-instance"


def _make_hotkey_listener(actions, hotkeys):
    """构建全局热键监听器。配置的热键组合非法（pynput 解析失败）时
    回退默认热键并记日志，不阻断启动。

    actions: {动作名: 回调}，动作名与 config.DEFAULT_HOTKEYS 的键一致。
    hotkeys: {热键组合: 回调}（来自用户配置，空组合已在配置层剔除）。
    """
    try:
        return kb.GlobalHotKeys(hotkeys)
    except Exception as e:
        get_logger().error("热键配置非法，回退默认热键：%s", e)
        return kb.GlobalHotKeys(
            {DEFAULT_HOTKEYS[k]: fn for k, fn in actions.items()})


def _acquire_single_instance_mutex(name=_SINGLE_INSTANCE_MUTEX):
    """单实例保护：可运行返回互斥体句柄，已有实例运行返回 None。

    句柄须在进程生命周期内持有（关闭句柄即释放互斥体对象）。
    Local\\ 命名空间按登录会话隔离，不同会话互不影响。
    CreateMutexW 本身失败（极端异常）时返回 True 放行，不阻断启动。
    """
    import ctypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        return True
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        return None
    return handle


def main():
    """主入口。

    - 主线程：QApplication 事件循环（Windows 消息泵）+ pynput 快捷键监听。
      pynput 监听线程自带消息循环，主线程只需有事件循环存活。
    - 子线程：pystray 托盘（阻塞）、耗时的截图/保存/剪贴板。
    - 所有 Qt 窗口（选区等）都在主线程创建、由 Qt 事件循环驱动；
      子线程需要触达 GUI 时通过 _run_on_main 调度回主线程。
    """
    mutex = _acquire_single_instance_mutex()
    if mutex is None:
        get_logger().info("已有实例在运行，本实例退出")
        return
    # 持有句柄至进程结束：提前释放会让第二个实例得以启动
    _gui["mutex"] = mutex
    get_logger().info("screen-shot 启动")

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

    actions = {
        "region": do_region_capture,
        "fullscreen": do_fullscreen_capture,
        "quit": on_quit,
    }
    hk_cfg = get_config()["hotkeys"]
    hotkeys = {hk_cfg[k]: fn for k, fn in actions.items() if hk_cfg.get(k)}
    with _make_hotkey_listener(actions, hotkeys):
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
