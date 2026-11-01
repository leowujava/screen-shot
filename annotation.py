"""选区后内联标注窗口（Approach B）。

全屏不透明 Toplevel：
- 背景 = 全屏截图（只贴一次）
- 选区外 = 4 块半透明暗化遮罩（PIL 预渲染，alpha=128）
- 选区内 = 明亮，显示 region_img（标注画在这上面）
- 选区边框 + 8 个手柄（可调整选区，调整后标注作废）
- 工具条：画笔/矩形/椭圆/箭头/打码/文字 + 颜色 + 粗细 + 撤销 + 确认/复制/保存/取消

标注后：
- 确认 → on_confirm(region_img)
- 复制 → on_copy(region_img)
- 保存 → on_save(region_img)
- 取消 → on_cancel()
"""
import sys
import tkinter as tk
from tkinter import colorchooser
from PIL import Image, ImageTk

from drawing import (
    draw_arrow, draw_rect, draw_ellipse, draw_brush, draw_text, mosaic, UndoStack,
)

# 主题色（与 editor.py / selector.py 一致）
TB_BG = "#2b2f36"
TB_FG = "#f5f6f8"
TB_HOVER = "#4a90e2"
TB_HOVER_FG = "#ffffff"
TB_SEP = "#4a5160"
TB_FONT = ("Microsoft YaHei UI", 9)
LABEL_BG = "#2b2f36"
LABEL_FG = "#f5f6f8"

# 暗化遮罩 alpha（0-255，128 ≈ 50%）
MASK_ALPHA = 128


class AnnotationWindow:
    TOOLS = ("brush", "rect", "ellipse", "arrow", "mosaic", "text")
    TOOL_ICONS = {"brush": "✏", "rect": "▭", "ellipse": "◯", "arrow": "↗",
                  "mosaic": "▦", "text": "T"}
    TOOL_LABELS = {"brush": "画笔", "rect": "矩形", "ellipse": "圆形",
                   "arrow": "箭头", "mosaic": "打码", "text": "文字"}

    MIN_SIZE = 5
    HANDLE_SIZE = 12
    MARGIN = 8
    HANDLES = ("nw", "n", "ne", "e", "se", "s", "sw", "w")
    _CURSORS = {"nw": "fleur", "n": "sb_up_arrow", "ne": "crosshair",
                "e": "sb_right_arrow", "se": "fleur", "s": "sb_down_arrow",
                "sw": "crosshair", "w": "sb_left_arrow", "move": "fleur"}

    def __init__(self, full_img, region, on_confirm, on_copy, on_save,
                 on_cancel, root=None):
        self.full_img = full_img
        self.region = region  # (left, top, w, h) 虚拟桌面坐标
        self.on_confirm = on_confirm
        self.on_copy = on_copy
        self.on_save = on_save
        self.on_cancel = on_cancel
        self._owns_root = root is None

        # 选区 crop
        l, t, w, h = region
        self.region_img = full_img.crop((l, t, l + w, t + h))
        self.original_region = self.region_img.copy()

        # 标注状态
        self.tool = "brush"
        self.color = "#ff0000"
        self.width = 3
        self.undo = UndoStack(maxlen=10)
        self.drawing = False
        self.draw_start = None
        self._brush_last = None
        self._preview_id = None
        self._tool_btns = {}

        # 选区调整状态（canvas 坐标 = 虚拟桌面坐标）
        self._mode = "adjust"  # "adjust" = 可调整选区 + 标注
        self._region_rect = None  # (l, t, r, b) canvas 坐标
        self._drag_op = None
        self._drag_start = None
        self._drag_region = None
        self._items = {}
        self._finished = False

        # 工具条状态
        self._tb_visible = False
        self._tb_size = None

        # PhotoImage 引用（保持引用，防止被 GC）
        self._bg_photo = None
        self._region_photo = None
        self._mask_photos = []

        self._shared_root = root

        # 窗口
        self._build_ui()

    # ------------------------------------------------------------------ #
    # 窗口 / UI
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        if self._owns_root:
            self.root = tk.Tk()
            self.root.title("")
        else:
            self.root = tk.Toplevel(self._shared_root)
            self.root.title("")

        self.root.configure(bg="black")
        self.root.wm_attributes("-topmost", True)
        self.root.wm_overrideredirect(True)

        # 全屏（与 selector 相同的 Win32 强制扩展逻辑）
        self._actual_w, self._actual_h = self._get_screen_size()
        self.root.geometry(f"{self._actual_w}x{self._actual_h}+0+0")
        self._force_fullscreen(0, 0, self._actual_w, self._actual_h)
        self.root.update_idletasks()
        self._force_fullscreen(0, 0, self._actual_w, self._actual_h)

        self.canvas = tk.Canvas(self.root, bg="black", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.root.bind("<Escape>", lambda e: self._on_cancel())
        self.root.bind("<Return>", lambda e: self._on_confirm())
        self.root.bind("<Control-z>", lambda e: self._undo())
        self.root.bind("<Control-Z>", lambda e: self._undo())

        self._build_toolbar()
        self.root.update_idletasks()

        # 初始渲染：背景 + 遮罩 + 选区图 + 边框 + 手柄 + 工具条
        l, t, w, h = self.region
        self._region_rect = (l, t, l + w, t + h)
        self._render_all()

    def _get_screen_size(self):
        """动态获取虚拟桌面尺寸，与 selector 一致。"""
        if sys.platform == "win32":
            try:
                import ctypes
                user32 = ctypes.windll.user32
                w = user32.GetSystemMetrics(78)
                h = user32.GetSystemMetrics(79)
                if w > 0 and h > 0:
                    return w, h
            except Exception:
                pass
        try:
            w = self.root.winfo_screenwidth()
            h = self.root.winfo_screenheight()
            if isinstance(w, int) and isinstance(h, int) and w > 0 and h > 0:
                return w, h
        except (tk.TclError, TypeError):
            pass
        return (1920, 1080)

    def _force_fullscreen(self, x, y, width, height):
        """用 Win32 强制把窗口扩展到完整虚拟桌面（与 selector 一致）。"""
        if sys.platform != "win32":
            return
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            hwnd = wintypes.HWND(self.root.winfo_id())
            user32.SetWindowPos(
                hwnd, wintypes.HWND(-1),
                x, y, width, height,
                0,
            )
        except Exception:
            pass

    def show(self):
        """运行窗口。共享根窗口时非阻塞。"""
        if self._owns_root:
            self.root.mainloop()

    # ------------------------------------------------------------------ #
    # 渲染
    # ------------------------------------------------------------------ #

    def _render_all(self):
        """渲染/更新全部：背景、遮罩、选区图、边框、手柄、工具条。"""
        try:
            l, t, r, b = self._region_rect
        except (TypeError, ValueError):
            return

        # 背景（只贴一次）
        if "bg" not in self._items:
            try:
                bg_photo = ImageTk.PhotoImage(self.full_img)
            except Exception:
                return
            self._bg_photo = bg_photo
            self._items["bg"] = self.canvas.create_image(
                0, 0, image=bg_photo, anchor="nw")

        # 4 块暗化遮罩（PIL 预渲染，半透明黑色）
        self._render_masks(l, t, r, b)

        # 选区图（region_img → PhotoImage，贴在选区左上角）
        self._render_region(l, t)

        # 选区边框
        if "rect" not in self._items:
            self._items["rect"] = self.canvas.create_rectangle(
                0, 0, 0, 0, outline="white", width=2)
        self.canvas.coords(self._items["rect"], l, t, r, b)

        # 手柄
        self._render_handles(l, t, r, b)

        # 工具条
        self._tb_show((l, t, r, b), (l, t, r - l, b - t))

    def _render_masks(self, l, t, r, b):
        """重建 4 块半透明暗化遮罩（选区外）。"""
        W, H = self._actual_w, self._actual_h
        # 4 块区域的坐标：左上、右上、左下、右下
        zones = [
            (0, 0, l, t),
            (r, 0, W, t),
            (0, b, l, H),
            (r, b, W, H),
        ]
        for i, (zx0, zy0, zx1, zy1) in enumerate(zones):
            key = f"mask{i + 1}"
            zw, zh = zx1 - zx0, zy1 - zy0
            if zw <= 0 or zh <= 0:
                if key in self._items:
                    try:
                        self.canvas.delete(self._items[key])
                    except tk.TclError:
                        pass
                    del self._items[key]
                continue
            try:
                mask = Image.new("RGBA", (zw, zh), (0, 0, 0, MASK_ALPHA))
                photo = ImageTk.PhotoImage(mask)
            except Exception:
                continue
            self._mask_photos.append(photo)
            if key in self._items:
                try:
                    self.canvas.delete(self._items[key])
                except tk.TclError:
                    pass
            self._items[key] = self.canvas.create_image(
                zx0, zy0, image=photo, anchor="nw")

    def _render_region(self, l, t):
        """更新选区 PhotoImage（region_img → canvas）。"""
        try:
            photo = ImageTk.PhotoImage(self.region_img)
        except Exception:
            return
        self._region_photo = photo
        if "region" in self._items:
            try:
                self.canvas.delete(self._items["region"])
            except tk.TclError:
                pass
        self._items["region"] = self.canvas.create_image(
            l, t, image=photo, anchor="nw")
        self.canvas.tag_raise(self._items["region"])

    def _render_handles(self, l, t, r, b):
        """渲染 8 个手柄。"""
        s = self.HANDLE_SIZE
        coords = {
            "nw": (l, t), "n": ((l + r) // 2, t), "ne": (r, t),
            "e": (r, (t + b) // 2), "se": (r, b),
            "s": ((l + r) // 2, b), "sw": (l, b), "w": (l, (t + b) // 2),
        }
        if "handles" not in self._items:
            handles = {}
            for name, (hx, hy) in coords.items():
                handles[name] = self.canvas.create_rectangle(
                    hx - s, hy - s, hx - s + 2 * s, hy - s + 2 * s,
                    fill="#ffffff", outline=TB_BG, width=1)
            self._items["handles"] = handles
        else:
            h = self._items["handles"]
            for name, (hx, hy) in coords.items():
                self.canvas.coords(h[name], hx - s, hy - s, hx - s + 2 * s, hy - s + 2 * s)
        self.canvas.tag_raise(self._items["region"])

    # ------------------------------------------------------------------ #
    # 选区调整
    # ------------------------------------------------------------------ #

    def _adjust_region(self, l, t, r, b):
        """调整选区后重新 crop、清空标注和撤销栈。"""
        l, t = min(l, t), min(t, b)
        l, r = min(l, r), max(l, r)
        t, b = min(t, b), max(t, b)
        W, H = self._actual_w, self._actual_h
        l = max(0, min(l, W))
        t = max(0, min(t, H))
        r = max(0, min(r, W))
        b = max(0, min(b, H))
        if r - l < self.MIN_SIZE or b - t < self.MIN_SIZE:
            return
        self.region = (l, t, r - l, b - t)
        self._region_rect = (l, t, r, b)
        self.region_img = self.full_img.crop((l, t, r, b))
        self.original_region = self.region_img.copy()
        self.undo.clear()
        self._render_all()

    def _handle_coords(self, l, t, r, b):
        return {
            "nw": (l, t), "n": ((l + r) // 2, t), "ne": (r, t),
            "e": (r, (t + b) // 2), "se": (r, b),
            "s": ((l + r) // 2, b), "sw": (l, b), "w": (l, (t + b) // 2),
        }

    def _hit_test(self, x, y):
        """判断点 (x, y) 落在哪个手柄，返回手柄名或 None。"""
        l, t, r, b = self._region_rect
        s = self.HANDLE_SIZE
        m = self.MARGIN
        for name, (hx, hy) in self._handle_coords(l, t, r, b).items():
            if abs(x - hx) <= s + m and abs(y - hy) <= s + m:
                return name
        return None

    # ------------------------------------------------------------------ #
    # 鼠标事件
    # ------------------------------------------------------------------ #

    def _on_press(self, event):
        x, y = event.x, event.y
        # 1. 手柄 → 调整选区
        hit = self._hit_test(x, y)
        if hit:
            self._drag_op = (hit, "resize")
            self._drag_start = (x, y)
            self._drag_region = self._region_rect
            self.canvas.configure(cursor=self._CURSORS[hit])
            return
        # 2. 选区内 → 开始标注
        l, t, r, b = self._region_rect
        if l <= x <= r and t <= y <= b:
            rx, ry = x - l, y - t
            if self.tool == "text":
                self._input_text(rx, ry)
                return
            # 开始绘制
            self.drawing = True
            self.draw_start = (rx, ry)
            self._brush_last = (rx, ry)
            self.undo.push(self.region_img.copy())
            self.canvas.configure(cursor="crosshair")
            return
        # 3. 选区外（非手柄）→ 无操作

    def _on_drag(self, event):
        x, y = event.x, event.y
        # 选区调整
        if self._drag_op is not None and self._drag_start is not None:
            op, kind = self._drag_op
            dx = x - self._drag_start[0]
            dy = y - self._drag_start[1]
            l, t, r, b = self._drag_region
            if op in ("w", "sw", "nw"):
                l = l + dx
            if op in ("e", "ne", "se"):
                r = r + dx
            if op in ("n", "nw", "ne"):
                t = t + dy
            if op in ("s", "sw", "se"):
                b = b + dy
            self._adjust_region(l, t, r, b)
            return
        # 标注
        if self.drawing:
            l, t, r, b = self._region_rect
            rx, ry = x - l, y - t
            rx = max(0, min(rx, r - l - 1))
            ry = max(0, min(ry, b - t - 1))
            if self.tool == "brush":
                draw_brush(self.region_img,
                           [self._brush_last, (rx, ry)], self.color, self.width)
                self._brush_last = (rx, ry)
                self._render_region(l, t)
            else:
                sx, sy = self.draw_start
                self._draw_preview(sx + l, sy + t, rx + l, ry + t)

    def _on_release(self, event):
        x, y = event.x, event.y
        self._drag_op = None
        self._drag_start = None
        self._drag_region = None
        self.canvas.configure(cursor="crosshair")
        if not self.drawing:
            return
        self.drawing = False
        l, t, r, b = self._region_rect
        rx, ry = x - l, y - t
        rx = max(0, min(rx, r - l - 1))
        ry = max(0, min(ry, b - t - 1))
        sx, sy = self.draw_start
        self._clear_preview()
        if self.tool == "brush":
            pass  # 已在 drag 中完成
        elif self.tool == "rect":
            draw_rect(self.region_img,
                      [min(sx, rx), min(sy, ry), max(sx, rx), max(sy, ry)],
                      self.color, self.width)
        elif self.tool == "ellipse":
            if abs(rx - sx) < 1 and abs(ry - sy) < 1:
                return
            draw_ellipse(self.region_img,
                         [min(sx, rx), min(sy, ry), max(sx, rx), max(sy, ry)],
                         self.color, self.width)
        elif self.tool == "arrow":
            if abs(rx - sx) < 1 and abs(ry - sy) < 1:
                return
            draw_arrow(self.region_img, (sx, sy), (rx, ry), self.color, self.width)
        elif self.tool == "mosaic":
            self.region_img = mosaic(
                self.region_img,
                min(sx, rx), min(sy, ry), max(sx, rx), max(sy, ry),
                block=max(2, self.width // 2 or 4))
        self._render_region(l, t)

    def _draw_preview(self, x0, y0, x1, y1):
        """在 canvas 上画临时预览（canvas 坐标）。"""
        self._clear_preview()
        l, t, r, b = self._region_rect
        # 只画在选区内的部分
        px0 = max(l, min(x0, x1))
        py0 = max(t, min(y0, y1))
        px1 = min(r, max(x0, x1))
        py1 = min(b, max(y0, y1))
        if px1 - px0 < 1 or py1 - py0 < 1:
            return
        if self.tool == "rect":
            self._preview_id = self.canvas.create_rectangle(
                px0, py0, px1, py1, outline=self.color, width=self.width)
        elif self.tool == "ellipse":
            self._preview_id = self.canvas.create_oval(
                px0, py0, px1, py1, outline=self.color, width=self.width)
        elif self.tool == "arrow":
            self._preview_id = self.canvas.create_line(
                px0, py0, px1, py1, fill=self.color, width=self.width,
                arrow="last")
        elif self.tool == "mosaic":
            self._preview_id = self.canvas.create_rectangle(
                px0, py0, px1, py1, outline=self.color, width=1)
        try:
            self.canvas.tag_raise(self._preview_id)
        except (tk.TclError, AttributeError):
            pass

    def _clear_preview(self):
        if self._preview_id is not None:
            try:
                self.canvas.delete(self._preview_id)
            except (tk.TclError, AttributeError):
                pass
            self._preview_id = None

    # ------------------------------------------------------------------ #
    # 文字输入
    # ------------------------------------------------------------------ #

    def _input_text(self, x, y):
        text = self._input_text_dialog(x, y)
        if text:
            self.undo.push(self.region_img.copy())
            draw_text(self.region_img, (x, y), text, self.color, 16)
            self._render_all()

    def _input_text_dialog(self, x, y):
        """弹出文字输入框，返回输入文本（空则 None）。Esc 只关输入框。"""
        top = tk.Toplevel(self.root)
        top.title("输入文字")
        top.geometry("300x80")
        top.transient(self.root)
        top.grab_set()
        entry = tk.Entry(top, font=("Arial", 12))
        entry.pack(padx=8, pady=8, fill="x", expand=True)
        entry.focus_set()
        result = {"text": None}

        def commit(event=None):
            text = entry.get()
            result["text"] = text if text else None
            top.destroy()

        entry.bind("<Return>", commit)
        tk.Button(top, text="确定", command=commit).pack(pady=4)
        entry.bind("<Escape>", lambda e: top.destroy())
        top.bind("<Escape>", lambda e: top.destroy())
        top.grab_set()
        top.wait_window(top)
        return result["text"]

    # ------------------------------------------------------------------ #
    # 撤销
    # ------------------------------------------------------------------ #

    def _undo(self):
        prev = self.undo.pop()
        if prev is not None:
            self.region_img = prev
            self._render_all()

    # ------------------------------------------------------------------ #
    # 工具条
    # ------------------------------------------------------------------ #

    def _build_toolbar(self):
        self.tb_win = tk.Toplevel(self.root)
        self.tb_win.wm_overrideredirect(True)
        self.tb_win.configure(bg=TB_BG, bd=1, relief="solid")
        self.tb_win.withdraw()

        bar = tk.Frame(self.tb_win, bg=TB_BG)
        bar.pack(fill="x", padx=4, pady=3)

        # 工具按钮
        for tool in self.TOOLS:
            b = tk.Label(bar, text=self.TOOL_ICONS[tool],
                         fg=TB_FG, bg=TB_BG, font=TB_FONT, padx=6, pady=2,
                         cursor="hand2")
            b.pack(side="left", padx=1)
            b.bind("<Enter>", lambda e, bb=b: bb.configure(bg=TB_HOVER, fg=TB_HOVER_FG))
            b.bind("<Leave>", lambda e, bb=b: bb.configure(bg=TB_BG, fg=TB_FG))
            b.bind("<Button-1>", lambda e, t=tool: self._select_tool(t))
            self._tool_btns[tool] = b

        # 分隔
        sep = tk.Frame(bar, width=1, height=18, bg=TB_SEP)
        sep.pack(side="left", padx=5)

        # 颜色
        color_btn = tk.Label(bar, text="🎨", fg=TB_FG, bg=TB_BG, font=TB_FONT,
                             padx=4, pady=2, cursor="hand2")
        color_btn.pack(side="left", padx=1)
        color_btn.bind("<Enter>", lambda e: color_btn.configure(bg=TB_HOVER, fg=TB_HOVER_FG))
        color_btn.bind("<Leave>", lambda e: color_btn.configure(bg=TB_BG, fg=TB_FG))
        color_btn.bind("<Button-1>", lambda e: self._pick_color())

        # 粗细
        self._width_var = tk.StringVar(master=self.root, value="3")
        spin = tk.Spinbox(self.tb_win, from_=1, to=30, width=3,
                          textvariable=self._width_var, font=TB_FONT,
                          bg=TB_BG, fg=TB_FG, bd=0, relief="flat")
        spin.pack(side="left", padx=2, in_=bar)
        spin.bind("<FocusOut>", lambda e: self._on_width_change())
        spin.bind("<Return>", lambda e: self._on_width_change())

        # 撤销
        undo_btn = tk.Label(bar, text="↩", fg=TB_FG, bg=TB_BG, font=TB_FONT,
                            padx=4, pady=2, cursor="hand2")
        undo_btn.pack(side="left", padx=2)
        undo_btn.bind("<Enter>", lambda e: undo_btn.configure(bg=TB_HOVER, fg=TB_HOVER_FG))
        undo_btn.bind("<Leave>", lambda e: undo_btn.configure(bg=TB_BG, fg=TB_FG))
        undo_btn.bind("<Button-1>", lambda e: self._undo())

        # 分隔
        sep2 = tk.Frame(bar, width=1, height=18, bg=TB_SEP)
        sep2.pack(side="left", padx=5)

        # 确认/复制/保存/取消
        self._tb_btn(bar, "✓ 确认", lambda: self._on_confirm())
        self._tb_btn(bar, "⧉ 复制", lambda: self._on_copy())
        self._tb_btn(bar, "💾 保存", lambda: self._on_save())
        self._tb_btn(bar, "✕ 取消", lambda: self._on_cancel())

        self.tb_win.bind("<Escape>", lambda e: self._on_cancel())
        self.tb_win.bind("<Return>", lambda e: self._on_confirm())

    def _tb_btn(self, parent, text, command):
        b = tk.Label(parent, text=text, fg=TB_FG, bg=TB_BG, font=TB_FONT,
                     padx=6, pady=2, cursor="hand2")
        b.pack(side="left", padx=1)
        b.bind("<Enter>", lambda e, bb=b: bb.configure(bg=TB_HOVER, fg=TB_HOVER_FG))
        b.bind("<Leave>", lambda e, bb=b: bb.configure(bg=TB_BG, fg=TB_FG))
        b.bind("<Button-1>", lambda e, c=command: c())
        return b

    def _select_tool(self, name):
        self.tool = name
        for tool, b in self._tool_btns.items():
            if tool == name:
                b.configure(bg=TB_HOVER, fg=TB_HOVER_FG)
            else:
                b.configure(bg=TB_BG, fg=TB_FG)

    def _pick_color(self):
        color = colorchooser.askcolor(
            initialcolor=self.color,
            title="选择颜色",
            parent=self.root)
        if color and color[1]:
            self.color = color[1]

    def _on_width_change(self):
        try:
            val = int(self._width_var.get())
            self.width = max(1, min(30, val))
        except (ValueError, tk.TclError):
            pass

    def _tb_show(self, pos, size):
        """在选区下方显示工具条（与 selector 逻辑一致）。"""
        if not self._tb_visible:
            if not self.tb_win.winfo_exists():
                return
            self.tb_win.deiconify()
            self.tb_win.update_idletasks()
            self._tb_visible = True
            self._tb_size = (self.tb_win.winfo_width(), self.tb_win.winfo_height())
        x, y = pos[0], pos[1]
        w, h = size[0], size[1]
        tb_w, tb_h = self._tb_size
        W, H = self._actual_w, self._actual_h
        ty = y + h + 4
        if ty + tb_h > H:
            ty = y - tb_h - 4
        if ty < 0:
            ty = y + h + 4
        tx = max(0, min(x, W - tb_w)) if tb_w else x
        self.tb_win.geometry(f"+{tx}+{ty}")

    def _tb_hide(self):
        self._tb_visible = False
        self._tb_size = None
        if self.tb_win is not None:
            try:
                self.tb_win.withdraw()
            except tk.TclError:
                pass

    # ------------------------------------------------------------------ #
    # 结果输出
    # ------------------------------------------------------------------ #

    def _on_confirm(self):
        self._finish(self.on_confirm)

    def _on_copy(self):
        self._finish(self.on_copy)

    def _on_save(self):
        self._finish(self.on_save)

    def _on_cancel(self):
        self._finish_cancel()

    def _finish(self, callback):
        if self._finished:
            return
        self._finished = True
        self._tb_hide()
        callback(self.region_img)
        self._exit()

    def _finish_cancel(self):
        if self._finished:
            return
        self._finished = True
        self._tb_hide()
        self.on_cancel()
        self._exit()

    def _exit(self):
        if self._owns_root:
            try:
                self.root.destroy()
                self.tb_win.destroy()
            except tk.TclError:
                pass
        else:
            try:
                self.root.withdraw()
                self.tb_win.withdraw()
            except tk.TclError:
                pass
        # 清理画布
        for item in self._items.values():
            if isinstance(item, dict):
                for child in item.values():
                    try:
                        self.canvas.delete(child)
                    except tk.TclError:
                        pass
            elif item:
                try:
                    self.canvas.delete(item)
                except tk.TclError:
                    pass
        self._items.clear()
