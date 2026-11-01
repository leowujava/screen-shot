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
    """选区 + 标注结果 → 复制到剪贴板并保存。region 为 None 时不做任何事。

    确认后不再打开 Editor，直接在 Selector 中完成标注后复制 + 保存。
    region: (left, top, width, height) 虚拟桌面坐标
    annotated_img: PIL Image（选区 crop + 标注）
    必须在主线程（Tk 主循环所在线程）调用。
    """
    if region is None or annotated_img is None:
        return
    _safe_copy_to_clipboard(annotated_img)
    save_image(annotated_img, None)


def _virtual_screen_origin():
    """返回虚拟桌面左上角 (x, y)。"""
    user32 = ctypes.windll.user32
    return (user32.GetSystemMetrics(76), user32.GetSystemMetrics(77))


def do_region_capture():
    """框选区域截图（供快捷键和托盘调用）。

    主线程正被 Tk 主循环 + 快捷键消息泵占用，不能阻塞，
    因此耗时的整屏抓取放到工作线程，完成后把选区窗口调度回主线程。
    使用虚拟桌面（所有屏幕合并），遮罩层覆盖全部屏幕。
    """
    def worker():
        full = capture_fullscreen()
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
                full_img=full,
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
        try:
            copy_to_clipboard(full)
        except Exception:
            pass
    threading.Thread(target=worker, daemon=True).start()


def _tray_icon_rect():
    """返回托盘图标在虚拟桌面中的屏幕矩形 (left, top, right, bottom)。

    通过枚举系统托盘通知区域，匹配本进程图标所在按钮。失败时返回 None。
    """
    import ctypes.wintypes as wt
    user32 = ctypes.windll.user32

    taskbar = user32.FindWindowW("Shell_TrayWnd", None)
    if not taskbar:
        return None
    notify = user32.FindWindowExW(taskbar, 0, "SystemTray.Notify", None)
    if not notify:
        notify = user32.FindWindowExW(taskbar, 0, "TrayNotifyWnd", None)
    if not notify:
        return None
    toolbar = user32.FindWindowExW(notify, 0, "ToolbarWindow32", None)
    if not toolbar:
        return None

    pid = os.getpid()
    hwnd_list = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def enum_cb(hwnd, lparam):
        wpid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
        if wpid.value == pid:
            hwnd_list.append(hwnd)
        return True

    user32.EnumChildWindows(toolbar, enum_cb, 0)

    for hwnd in hwnd_list:
        rect = wt.RECT()
        if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            pt = wt.POINT(rect.left, rect.top)
            user32.ClientToScreen(hwnd, ctypes.byref(pt))
            return (pt.x, pt.y, pt.x + (rect.right - rect.left),
                    pt.y + (rect.bottom - rect.top))
    return None


def _make_tray_icon_image():
    """生成托盘图标（相机外形）。"""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle([8, 20, 56, 52], outline="white", width=3)
    d.ellipse([22, 28, 42, 48], outline="white", width=3)
    d.rectangle([24, 14, 40, 20], fill="white")
    return img


class FloatingMenu:
    """悬浮菜单：在指定屏幕坐标处弹出无边框置顶菜单。

    items: [(label, command), ...]
    root:  共享 Tk 根窗口（菜单作为其 Toplevel 打开，由主循环驱动）
    """

    # 主题色（与编辑器保持一致）
    BG = "#2b2f36"
    FG = "#f5f6f8"
    HOVER = "#4a90e2"
    HOVER_FG = "#ffffff"
    SEP = "#3a3f47"
    FONT = ("Microsoft YaHei UI", 10)
    PAD_X = 14
    PAD_Y = 6
    MIN_W = 140

    def __init__(self, items, root):
        self.items = items
        self.root = root
        self.win = None
        self._buttons = []
        self._pos = (100, 100)

    def show_at(self, x, y):
        """在屏幕坐标 (x, y) 处弹出菜单（非阻塞）。"""
        self.close()
        self._pos = (x, y)
        self.win = tk.Toplevel(self.root)
        self.win.wm_overrideredirect(True)   # 无边框（不占用任务栏）
        self.win.wm_attributes("-topmost", True)
        self.win.configure(bg=self.BG, bd=1, relief="solid")
        self.win.geometry(f"+{x}+{y}")

        inner = tk.Frame(self.win, bg=self.BG)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        for i, (label, command) in enumerate(self.items):
            if label is None:  # 分隔线
                sep = tk.Frame(inner, height=1, bg=self.SEP, highlightthickness=0)
                sep.pack(fill="x", padx=6, pady=4)
                continue
            btn = tk.Label(
                inner, text=label, anchor="w",
                fg=self.FG, bg=self.BG,
                font=self.FONT, padx=self.PAD_X, pady=self.PAD_Y,
            )
            btn.pack(fill="x")
            btn.bind("<Enter>", lambda e, b=btn: b.configure(bg=self.HOVER, fg=self.HOVER_FG))
            btn.bind("<Leave>", lambda e, b=btn: b.configure(bg=self.BG, fg=self.FG))
            btn.bind("<Button-1>", lambda e, c=command: (c(), self.close()))
            self._buttons.append(btn)

        # 点击菜单外部时关闭
        self.win.bind("<FocusOut>", lambda e: self._close_on_outside())
        # 点击自身以外的任意位置：用全局鼠标钩子太侵入，改用 Esc + FocusOut
        self.win.bind("<Escape>", lambda e: self.close())

        self.win.update_idletasks()
        # 若超出屏幕右/下边缘则回拉
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        mw = self.win.winfo_width()
        mh = self.win.winfo_height()
        if x + mw > sw:
            self.win.geometry(f"+{sw - mw}+{y}")
        if y + mh > sh:
            self.win.geometry(f"+{x}+{sh - mh}")

        self.win.lift()
        self.win.focus_force()

    def _close_on_outside(self):
        """FocusOut 时，若鼠标已不在菜单内则关闭。"""
        if self.win is None:
            return
        # 延迟判断，避免菜单内部焦点切换误触发
        self.root.after(50, self._check_outside)

    def _check_outside(self):
        if self.win is None:
            return
        try:
            # 用鼠标当前位置判断是否在菜单窗口内
            import pynput.mouse as _pm
            x, y = _pm.Position()
            wx = self.win.winfo_rootx()
            wy = self.win.winfo_rooty()
            ww = self.win.winfo_width()
            wh = self.win.winfo_height()
            if not (wx <= x <= wx + ww and wy <= y <= wy + wh):
                self.close()
        except Exception:
            pass

    def close(self):
        if self.win is not None:
            try:
                self.win.destroy()
            except tk.TclError:
                pass
            self.win = None
            self._buttons = []


def main():
    """主入口。

    - 主线程：创建共享 Tk 根窗口 + 运行 pynput 快捷键监听（两者都要求主线程），
      最后进入 Tk mainloop 驱动所有 GUI 窗口。
    - 子线程：pystray 托盘（阻塞）、耗时的截图/保存/剪贴板。
    - 所有 GUI（选区、编辑器、悬浮菜单）都挂到同一个共享根窗口上，
      避免多个 Tk 根互相破坏 Tcl 解释器（image 丢失、窗口卡住）。
    """
    tray_holder = {}
    _menu_holder = {}

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

    def _get_menu():
        if "menu" not in _menu_holder:
            items = [
                ("📷 框选截图", do_region_capture),
                ("🖥 全屏截图", do_fullscreen_capture),
                ("📋 复制截图", do_copy_capture),
                (None, None),  # 分隔线
                ("✕ 退出", on_quit),
            ]
            _menu_holder["menu"] = FloatingMenu(items, _tk_root_holder["root"])
        return _menu_holder["menu"]

    def _show_floating_menu():
        """在托盘图标位置弹出悬浮菜单（调度回主线程执行）。"""
        def _show():
            menu = _get_menu()
            rect = _tray_icon_rect()
            if rect:
                x, y, _, _ = rect
                # 菜单从图标底部弹出
                menu.show_at(x, y + 40)
            else:
                # 回退：屏幕右下角
                sw = _tk_root_holder["root"].winfo_screenwidth()
                sh = _tk_root_holder["root"].winfo_screenheight()
                menu.show_at(sw - 200, sh - 200)
        _run_on_main(_show)

    def run_tray():
        icon = pystray.Icon(
            "screen-shot",
            _make_tray_icon_image(),
            "截图工具",
            pystray.Menu(
                pystray.MenuItem("框选截图", lambda i, m: _show_floating_menu(), default=True),
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
