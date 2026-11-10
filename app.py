import ctypes
import os
import sys
import threading
import time
import tkinter as tk
from PIL import Image, ImageDraw
import pynput.keyboard as kb
import pystray
from capture import (
    capture_fullscreen,
    capture_region,
    capture_virtual_screen_size,
    copy_to_clipboard,
    save_image,
)
from selector import Selector

# 全局持有的共享 Tk 根窗口（在 main 的主线程创建）
_tk_root_holder = {}


def _run_on_main(fn):
    """在持有 Tk 主循环的主线程上执行 fn。

    从快捷键（监听线程）或托盘（pystray 线程）调用时，必须把 GUI 操作
    调度回主线程，否则会破坏 Tcl 解释器（image 丢失、窗口卡住等）。
    """
    holder = _tk_root_holder
    root = holder.get("root")
    if root is None:
        # 主循环尚未启动（理论上不会发生），退回直接执行
        fn()
        return
    root.after(0, fn)


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
        try:
            from tkinter import messagebox
            messagebox.showerror("复制失败", str(e))
        except Exception:
            pass


def process_region_selection(region, annotated_img):
    """选区 + 标注结果 → 复制到剪贴板。region 为 None 时不做任何事。

    确认后不再打开 Editor，只在 Selector 中完成标注后复制到剪贴板（不自动保存）。
    region: (left, top, width, height) 虚拟桌面坐标
    annotated_img: PIL Image（选区 crop + 标注）
    必须在主线程（Tk 主循环所在线程）调用。
    """
    if region is None or annotated_img is None:
        return
    _safe_copy_to_clipboard(annotated_img)


def _virtual_screen_origin():
    """返回虚拟桌面左上角 (x, y)。"""
    user32 = ctypes.windll.user32
    return (user32.GetSystemMetrics(76), user32.GetSystemMetrics(77))


def do_region_capture():
    """框选区域截图（供快捷键和托盘调用）。

    先在工作线程获取桌面尺寸，再把选区窗口调度回主线程。
    画出初始选区后才隐藏窗口并抓取屏幕，避免唤出时过早固定画面。
    """
    def worker():
        vsize = capture_virtual_screen_size()
        vx, vy = _virtual_screen_origin()

        def _on_copy(region, annotated_img):
            if region is None:
                return
            _safe_copy_to_clipboard(annotated_img)

        def _on_save(region, annotated_img):
            if region is None:
                return
            save_image(annotated_img, None)

        def open_selector():
            # 选区窗口覆盖整个虚拟桌面（所有屏幕）
            Selector(
                x=vx, y=vy, width=vsize[0], height=vsize[1],
                full_img=None,
                capture_screen=capture_fullscreen,
                on_select=lambda region, img: process_region_selection(region, img),
                on_copy=_on_copy,
                on_save=_on_save,
                root=_tk_root_holder.get("root"),
            ).show()

        _run_on_main(open_selector)

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

    - 主线程：创建共享 Tk 根窗口 + 运行 pynput 快捷键监听（两者都要求主线程），
      最后进入 Tk mainloop 驱动所有 GUI 窗口。
    - 子线程：pystray 托盘（阻塞）、耗时的截图/保存/剪贴板。
    - 所有 GUI（选区等窗口）都挂到同一个共享根窗口上，
      避免多个 Tk 根互相破坏 Tcl 解释器（image 丢失、窗口卡住）。
    """
    tray_holder = {}

    def on_quit():
        """退出：停止托盘 → 退出主循环 → 强制结束进程。

        on_quit 可能从托盘线程或主线程调用；必须把 root.quit() 调度回主线程
        才能退出 mainloop。随后 os._exit 兜底，避免 onnxruntime 等非 daemon
        线程让进程卡住。
        """
        if "icon" in tray_holder:
            try:
                tray_holder["icon"].stop()
            except Exception:
                pass
        # 调度回主线程退出 mainloop
        _run_on_main(lambda: (
            _tk_root_holder["root"].quit(),
            # 延迟一帧让 mainloop 真正退出，再强制结束进程
            _tk_root_holder["root"].after(50, lambda: os._exit(0)),
        ))

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

    # 主线程：先创建共享 Tk 根窗口（Windows 要求 Tk 在主线程），
    # 随后在同一主线程运行 pynput 快捷键监听（它需要主线程的消息泵）。
    # 所有 GUI 窗口都挂在这个根上，统一由它的事件循环驱动；
    # 耗时的截图 / 保存 / 剪贴板等放到工作线程，完成后通过 after 调度回主线程。
    _tk_root_holder["root"] = tk.Tk()
    _tk_root_holder["root"].withdraw()

    with kb.GlobalHotKeys({
        "<ctrl>+<shift>+x": lambda: do_region_capture(),
        "<ctrl>+<shift>+s": lambda: do_fullscreen_capture(),
        "<ctrl>+<shift>+q": lambda: on_quit(),
    }):
        try:
            _tk_root_holder["root"].mainloop()
        except KeyboardInterrupt:
            pass
        # 清理：停止托盘、关闭窗口
        if "icon" in tray_holder:
            try:
                tray_holder["icon"].stop()
            except Exception:
                pass
        try:
            _tk_root_holder["root"].destroy()
        except Exception:
            pass
        os._exit(0)


if __name__ == "__main__":
    main()
