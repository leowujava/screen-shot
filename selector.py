import sys
import tkinter as tk

# 选区完成回调常量
SELECT_DONE = "done"      # 用户确认选区（Enter / 双击 / 工具条确认）
SELECT_CANCEL = "cancel"  # 用户取消（Esc / 右键 / 工具条取消）

# 深色工具条配色（与飞书截图工具条风格一致）
TB_BG = "#2b2f36"
TB_FG = "#f5f6f8"
TB_HOVER = "#4a90e2"
TB_HOVER_FG = "#ffffff"
TB_SEP = "#4a5160"
TB_FONT = ("Microsoft YaHei UI", 9)

# 尺寸标签配色
LABEL_BG = "#2b2f36"
LABEL_FG = "#f5f6f8"

# 十字准星颜色
CROSSHAIR_COLOR = "red"
CROSSHAIR_WIDTH = 1

# 暗化遮罩 alpha（0-255，128 ≈ 50%）
MASK_ALPHA = 128

from drawing import (
    draw_arrow, draw_rect, draw_ellipse, draw_brush, draw_text, mosaic, UndoStack,
)
from PIL import Image, ImageDraw, ImageTk


class Selector:
    """合并选区 + 标注：拖拽框选 → 完整工具条 → 画完框直接标注 → 确认/复制/保存/取消。

    内部两个模式：
    - select：按下左键前。全屏暗化，十字准星跟随鼠标，工具条工具按钮禁用。
    - adjust：按下左键后。出现选区框 + 8 个手柄，可拖动/缩放/标注。

    x, y: 窗口左上角在虚拟桌面中的坐标（使窗口 (0,0) 对齐虚拟桌面 (0,0)）
    width, height: 虚拟桌面尺寸
    full_img: 全屏截图（PIL Image），用于 adjust 模式背景和选区 crop
    on_select: 确认/取消回调，参数为 (region, annotated_img)；取消时 (None, None)
      region = (left, top, width, height) mss 虚拟桌面坐标
      annotated_img = PIL Image（选区 crop + 标注）
    on_copy: 复制回调，签名同 on_select
    on_save: 保存回调，签名同 on_select
    root: 可选的共享 Tk 根窗口。传入时选区作为其 Toplevel 打开（非阻塞）；
          不传则自建根窗口并阻塞运行（show）。
    """

    MIN_SIZE = 5
    HANDLE_SIZE = 12
    MARGIN = 8

    # 8 个手柄的标识
    HANDLES = ("nw", "n", "ne", "e", "se", "s", "sw", "w")

    # 各手柄的窗口光标（拖动方向提示）
    _CURSORS = {"nw": "fleur", "n": "sb_up_arrow", "ne": "crosshair",
                "e": "sb_right_arrow", "se": "fleur", "s": "sb_down_arrow",
                "sw": "crosshair", "w": "sb_left_arrow", "move": "fleur"}

    # 标注工具
    TOOLS = ("brush", "rect", "ellipse", "arrow", "mosaic", "text")
    TOOL_ICONS = {"brush": "✏", "rect": "▭", "ellipse": "◯", "arrow": "↗",
                  "mosaic": "▦", "text": "T"}
    TOOL_LABELS = {"brush": "画笔", "rect": "矩形", "ellipse": "圆形",
                   "arrow": "箭头", "mosaic": "打码", "text": "文字"}

    def __init__(self, x, y, width, height, full_img, on_select,
                 on_copy, on_save, root=None):
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.full_img = full_img
        self.on_select = on_select
        self.on_copy = on_copy
        self.on_save = on_save
        self.result = None
        self._owns_root = root is None
        self._mode = "select"
        self._items = {}
        self._region = None
        self._drag_op = None
        self._drag_start = None
        self._drag_region = None
        self._crosshair_h = None
        self._crosshair_v = None
        self._crosshair_label = None
        self._crosshair_label_bg = None
        self._crosshair_label_size = None
        self._bg_photo = None
        self._tb_visible = False
        self._tb_size = None

        # 标注状态
        self.tool = "brush"
        self.color = "#ff0000"
        self.width_attr = 3
        self.undo = UndoStack(maxlen=10)
        self.drawing = False
        self.draw_start = None
        self._brush_last = None
        self._preview_id = None
        self._tool_btns = {}
        self._tb_btns = {}
        self._region_img = None
        self._region_photo = None
        self._mask_photos = []

        if self._owns_root:
            self.root = tk.Tk()
            self.root.title("")
        else:
            self.root = tk.Toplevel(root)
            self.root.title("")

        # 动态计算实际屏幕大小（虚拟桌面）
        self._actual_w, self._actual_h = self._get_screen_size()
        self._scale_x = width / self._actual_w if self._actual_w else 1.0
        self._scale_y = height / self._actual_h if self._actual_h else 1.0

        self.root.geometry(f"{self._actual_w}x{self._actual_h}+{x}+{y}")
        self.root.configure(bg="black")
        self.root.wm_attributes("-topmost", True)
        self.root.wm_overrideredirect(True)
        self.root.attributes("-alpha", 0.5)
        self._force_fullscreen(x, y, self._actual_w, self._actual_h)
        self.root.update_idletasks()
        self._force_fullscreen(x, y, self._actual_w, self._actual_h)

        self.canvas = tk.Canvas(self.root, bg="black", highlightthickness=0,
                                cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Button-3>", lambda e: self._cancel())
        self.canvas.bind("<Double-Button-1>", lambda e: self._confirm())
        self.root.bind("<Escape>", lambda e: self._cancel())
        self.root.bind("<Return>", lambda e: self._confirm())
        self.root.bind("<Control-z>", lambda e: self._undo())
        self.root.bind("<Control-Z>", lambda e: self._undo())

        self._build_toolbar()
        self.tb_win.bind("<Escape>", lambda e: self._cancel())
        self.tb_win.bind("<Return>", lambda e: self._confirm())

        self.root.focus_force()
        self._finished = False
        # 初始 select 模式：工具按钮禁用
        self._set_toolbar_enabled(False)

    # ------------------------------------------------------------------ #
    # 屏幕尺寸
    # ------------------------------------------------------------------ #

    def _get_screen_size(self):
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
        return self.width, self.height

    def _force_fullscreen(self, x, y, width, height):
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
        tk.Frame(bar, width=1, height=18, bg=TB_SEP).pack(side="left", padx=5)

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
        tk.Frame(bar, width=1, height=18, bg=TB_SEP).pack(side="left", padx=5)

        # 确认/复制/保存/取消
        self._tb_btn(bar, "confirm", "✓ 确认", self._confirm)
        self._tb_btn(bar, "copy", "⧉ 复制", self._copy)
        self._tb_btn(bar, "save", "💾 保存", self._save)
        self._tb_btn(bar, "cancel", "✕ 取消", self._cancel)

    def _tb_btn(self, parent, key, text, command):
        b = tk.Label(parent, text=text, fg=TB_FG, bg=TB_BG, font=TB_FONT,
                     padx=6, pady=2, cursor="hand2")
        b.pack(side="left", padx=1)
        b.bind("<Enter>", lambda e, bb=b: bb.configure(bg=TB_HOVER, fg=TB_HOVER_FG))
        b.bind("<Leave>", lambda e, bb=b: bb.configure(bg=TB_BG, fg=TB_FG))
        b.bind("<Button-1>", lambda e, c=command: c())
        self._tb_btns[key] = b
        return b

    def _tb_show(self, pos, size):
        if not self._tb_visible:
            if not self.tb_win.winfo_exists():
                return
            self.tb_win.deiconify()
            self.tb_win.update_idletasks()
            self._tb_visible = True
            self._tb_size = (self.tb_win.winfo_width(), self.tb_win.winfo_height())
        x, y = pos[0], pos[1]
        w, h = size[2], size[3]
        tb_w, tb_h = self._tb_size
        W, H = self._actual_w, self._actual_h
        ty = y + h + 4
        if ty + tb_h > H:
            ty = y - tb_h - 4
        if ty < 0:
            ty = y + h + 4
        tx = max(0, min(x, W - tb_w)) if tb_w else x
        self.tb_win.geometry(f"+{self.x + tx}+{self.y + ty}")

    def _tb_hide(self):
        self._tb_visible = False
        self._tb_size = None
        if self.tb_win is not None and self.tb_win.winfo_exists():
            try:
                self.tb_win.withdraw()
            except tk.TclError:
                pass

    def _set_toolbar_enabled(self, enabled):
        """启用/禁用工具按钮。禁用时灰色 + 不响应。"""
        for tool, b in self._tool_btns.items():
            if enabled:
                b.configure(fg=TB_FG, bg=TB_BG, cursor="hand2")
                b.bind("<Button-1>", lambda e, t=tool: self._select_tool(t))
            else:
                b.configure(fg=TB_SEP, bg=TB_BG, cursor="arrow")
                b.bind("<Button-1>", lambda e: None)

    # ------------------------------------------------------------------ #
    # 十字准星
    # ------------------------------------------------------------------ #

    def _show_crosshair(self, x, y, w, h):
        W, H = self._actual_w, self._actual_h
        if self._crosshair_h is None:
            self._crosshair_h = self.canvas.create_line(
                0, y, W, y, fill=CROSSHAIR_COLOR, width=CROSSHAIR_WIDTH)
        else:
            self.canvas.coords(self._crosshair_h, 0, y, W, y)
        if self._crosshair_v is None:
            self._crosshair_v = self.canvas.create_line(
                x, 0, x, H, fill=CROSSHAIR_COLOR, width=CROSSHAIR_WIDTH)
        else:
            self.canvas.coords(self._crosshair_v, x, 0, x, H)
        text = f"{w} x {h}" if w > 0 and h > 0 else ""
        lx = x + 16
        ly = y + 16
        if lx + 80 > W:
            lx = x - 80
        if ly + 20 > H:
            ly = y - 20
        if self._crosshair_label is None:
            self._crosshair_label = self.canvas.create_text(
                lx, ly, text=text, fill=LABEL_FG, anchor="nw",
                font=("Microsoft YaHei UI", 9))
            self.canvas.tag_raise(self._crosshair_label)
            try:
                bbox = tuple(self.canvas.bbox(self._crosshair_label))
            except (TypeError, ValueError, tk.TclError):
                bbox = None
            if bbox:
                x1, y1, x2, y2 = bbox
                self._crosshair_label_bg = self.canvas.create_rectangle(
                    x1 - 4, y1 - 2, x2 + 4, y2 + 2,
                    fill=LABEL_BG, outline="", width=0)
                self.canvas.tag_raise(self._crosshair_label)
        else:
            self.canvas.coords(self._crosshair_label, lx, ly)
            self.canvas.itemconfig(self._crosshair_label, text=text)
            if self._crosshair_label_bg:
                size = self._crosshair_label_size
                if size is None:
                    try:
                        bbox = tuple(self.canvas.bbox(self._crosshair_label))
                        x1, y1, x2, y2 = bbox
                        self._crosshair_label_size = (x2 - x1, y2 - y1)
                        size = self._crosshair_label_size
                    except (TypeError, ValueError, tk.TclError):
                        size = None
                if size:
                    tw, th = size
                    self.canvas.coords(
                        self._crosshair_label_bg,
                        lx - 4, ly - 2, lx + tw + 4, ly + th + 2,
                    )

    def _hide_crosshair(self):
        for attr in ("_crosshair_h", "_crosshair_v", "_crosshair_label", "_crosshair_label_bg"):
            item = getattr(self, attr, None)
            if item is not None:
                try:
                    self.canvas.delete(item)
                except tk.TclError:
                    pass
                setattr(self, attr, None)
        self._crosshair_label_size = None

    # ------------------------------------------------------------------ #
    # 渲染
    # ------------------------------------------------------------------ #

    def _render_all(self):
        """渲染/更新全部。select 模式只画十字准星；adjust 模式画背景+遮罩+选区+边框+手柄+工具条。"""
        if self._mode != "adjust" or self._region is None:
            return
        l, t, r, b = self._region
        W, H = self._actual_w, self._actual_h

        # 背景（full_img 只贴一次）
        if "bg" not in self._items and self.full_img is not None:
            try:
                bg_photo = ImageTk.PhotoImage(self.full_img)
            except Exception:
                return
            self._bg_photo = bg_photo
            self._items["bg"] = self.canvas.create_image(0, 0, image=bg_photo, anchor="nw")

        # 4 块暗化遮罩
        self._render_masks(l, t, r, b)

        # 选区图
        self._render_region(l, t)

        # 选区边框
        if "rect" not in self._items:
            self._items["rect"] = self.canvas.create_rectangle(0, 0, 0, 0, outline="white", width=2)
        self.canvas.coords(self._items["rect"], l, t, r, b)

        # 手柄
        self._render_handles(l, t, r, b)

        # 工具条
        self._tb_show((l, t, r, b), (l, t, r - l, b - t))

    def _render_masks(self, l, t, r, b):
        """重建 4 块半透明暗化遮罩（选区外）。"""
        W, H = self._actual_w, self._actual_h
        zones = [
            (0, 0, l, t),
            (r, 0, W, t),
            (0, b, l, H),
            (r, b, W, H),
        ]
        self._mask_photos = []
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
        if self._region_img is None:
            return
        try:
            photo = ImageTk.PhotoImage(self._region_img)
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
        if "region" in self._items:
            self.canvas.tag_raise(self._items["region"])

    def _handle_coords(self, l, t, r, b):
        return {
            "nw": (l, t), "n": ((l + r) // 2, t), "ne": (r, t),
            "e": (r, (t + b) // 2), "se": (r, b),
            "s": ((l + r) // 2, b), "sw": (l, b), "w": (l, (t + b) // 2),
        }

    # ------------------------------------------------------------------ #
    # 选区 crop
    # ------------------------------------------------------------------ #

    def _init_region_img(self):
        """从 full_img crop 出 region_img。"""
        if self.full_img is None:
            return
        l, t, r, b = self._region
        self._region_img = self.full_img.crop((l, t, r, b))

    def _adjust_region(self):
        """调整选区后重新 crop、清空标注和撤销栈。"""
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
        self._render_all()

    # ------------------------------------------------------------------ #
    # 坐标 / 区域计算
    # ------------------------------------------------------------------ #

    def _to_virtual(self, wx, wy):
        return (wx + self.x, wy + self.y)

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

    # ------------------------------------------------------------------ #
    # 鼠标事件
    # ------------------------------------------------------------------ #

    def _hit_test(self, x, y):
        if self._mode != "adjust" or self._region is None:
            return None
        l, t, r, b = self._region
        s = self.HANDLE_SIZE // 2  # 6px 热区，避免标注误触手柄
        for name, (hx, hy) in self._handle_coords(l, t, r, b).items():
            if abs(x - hx) <= s and abs(y - hy) <= s:
                return name
        return None

    def _on_press(self, event):
        if self._mode == "select":
            # 按下立刻创建最小框并进入 adjust 模式
            self.start = (event.x, event.y)
            self._mode = "adjust"
            x, y = event.x, event.y
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
        hit = self._hit_test(event.x, event.y)
        if hit:
            self._drag_op = (hit, "resize")
            self._drag_start = (event.x, event.y)
            self._drag_region = self._region
            self.canvas.configure(cursor=self._CURSORS[hit])
            return
        # 选区内 → 开始标注
        l, t, r, b = self._region
        if l <= event.x <= r and t <= event.y <= b:
            if self._region_img is None:
                return
            rx, ry = event.x - l, event.y - t
            if self.tool == "text":
                self._input_text(rx, ry)
                self.drawing = False
                self.draw_start = None
                return
            self.drawing = True
            self.draw_start = (rx, ry)
            self._brush_last = (rx, ry)
            self.undo.push(self._region_img.copy())
            if self.tool == "brush":
                d = ImageDraw.Draw(self._region_img)
                d.point(self.draw_start, fill=self.color)
                self._render_region(l, t)
            self.canvas.configure(cursor="crosshair")

    def _on_drag(self, event):
        # select→adjust 过渡：更新框大小
        if self._mode == "adjust" and self._drag_op is None and getattr(self, "start", None):
            sx, sy = self.start
            x1, y1 = min(sx, event.x), min(sy, event.y)
            x2, y2 = max(sx, event.x), max(sy, event.y)
            W, H = self._actual_w, self._actual_h
            if sx <= 1 or event.x <= 1:
                x1 = 0
            if sy <= 1 or event.y <= 1:
                y1 = 0
            if sx >= W - 2 or event.x >= W - 2:
                x2 = W
            if sy >= H - 2 or event.y >= H - 2:
                y2 = H
            w = x2 - x1
            h = y2 - y1
            if w < self.MIN_SIZE and h < self.MIN_SIZE:
                return
            self._region = self._normalize((x1, y1, x2, y2))
            if self._region:
                l, t, r, b = self._region
                self._show_crosshair(event.x, event.y, r - l, b - t)
                self._init_region_img()
            self._render_all()
            return
        # 手柄 resize
        if self._mode == "adjust" and self._drag_op is not None and self._drag_start is not None:
            op, kind = self._drag_op
            dx = event.x - self._drag_start[0]
            dy = event.y - self._drag_start[1]
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
            self._region = self._normalize((l, t, r, b))
            self._hide_crosshair()
            self._adjust_region()
            return
        # 标注
        if self.drawing and self._region_img is not None:
            l, t, r, b = self._region
            rx, ry = event.x - l, event.y - t
            rx = max(0, min(rx, r - l - 1))
            ry = max(0, min(ry, b - t - 1))
            if self.tool == "brush":
                draw_brush(self._region_img, [self._brush_last, (rx, ry)], self.color, self.width_attr)
                self._brush_last = (rx, ry)
                self._render_region(l, t)
            else:
                sx, sy = self.draw_start
                self._draw_preview(sx + l, sy + t, rx + l, ry + t)

    def _on_release(self, event):
        self.start = None
        self._drag_op = None
        self._drag_start = None
        self._drag_region = None
        self.canvas.configure(cursor="crosshair")
        self._hide_crosshair()
        if self._region is None:
            self._mode = "select"
            self._render_all()
        if not self.drawing or self._region_img is None:
            return
        self.drawing = False
        l, t, r, b = self._region
        rx, ry = event.x - l, event.y - t
        rx = max(0, min(rx, r - l - 1))
        ry = max(0, min(ry, b - t - 1))
        sx, sy = self.draw_start
        self._clear_preview()
        if self.tool == "brush":
            pass
        elif self.tool == "rect":
            draw_rect(self._region_img,
                      [min(sx, rx), min(sy, ry), max(sx, rx), max(sy, ry)],
                      self.color, self.width_attr)
        elif self.tool == "ellipse":
            if abs(rx - sx) < 1 and abs(ry - sy) < 1:
                return
            draw_ellipse(self._region_img,
                         [min(sx, rx), min(sy, ry), max(sx, rx), max(sy, ry)],
                         self.color, self.width_attr)
        elif self.tool == "arrow":
            if abs(rx - sx) < 1 and abs(ry - sy) < 1:
                return
            draw_arrow(self._region_img, (sx, sy), (rx, ry), self.color, self.width_attr)
        elif self.tool == "mosaic":
            self._region_img = mosaic(
                self._region_img,
                min(sx, rx), min(sy, ry), max(sx, rx), max(sy, ry),
                block=max(2, self.width_attr // 2 or 4))
        self._render_region(l, t)

    def _draw_preview(self, x0, y0, x1, y1):
        self._clear_preview()
        l, t, r, b = self._region
        px0 = max(l, min(x0, x1))
        py0 = max(t, min(y0, y1))
        px1 = min(r, max(x0, x1))
        py1 = min(b, max(y0, y1))
        if px1 - px0 < 1 or py1 - py0 < 1:
            return
        if self.tool == "rect":
            self._preview_id = self.canvas.create_rectangle(
                px0, py0, px1, py1, outline=self.color, width=self.width_attr)
        elif self.tool == "ellipse":
            self._preview_id = self.canvas.create_oval(
                px0, py0, px1, py1, outline=self.color, width=self.width_attr)
        elif self.tool == "arrow":
            self._preview_id = self.canvas.create_line(
                px0, py0, px1, py1, fill=self.color, width=self.width_attr,
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
            self.undo.push(self._region_img.copy())
            draw_text(self._region_img, (x, y), text, self.color, 16)
            self._render_all()

    def _input_text_dialog(self, x, y):
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
    # 撤销 / 工具 / 颜色 / 粗细
    # ------------------------------------------------------------------ #

    def _undo(self):
        prev = self.undo.pop()
        if prev is not None:
            self._region_img = prev
            self._render_all()

    def _select_tool(self, name):
        self.tool = name
        for tool, b in self._tool_btns.items():
            if tool == name:
                b.configure(bg=TB_HOVER, fg=TB_HOVER_FG)
            else:
                b.configure(bg=TB_BG, fg=TB_FG)

    def _pick_color(self):
        from tkinter import colorchooser
        color = colorchooser.askcolor(
            initialcolor=self.color,
            title="选择颜色",
            parent=self.root)
        if color and color[1]:
            self.color = color[1]

    def _on_width_change(self):
        try:
            val = int(self._width_var.get())
            self.width_attr = max(1, min(30, val))
        except (ValueError, tk.TclError):
            pass

    # ------------------------------------------------------------------ #
    # 确认 / 取消 / 复制 / 保存
    # ------------------------------------------------------------------ #

    def _confirm(self):
        if self._region is None:
            self._cancel()
            return
        l, t, r, b = self._region
        vx_l = int(l * self._scale_x)
        vx_t = int(t * self._scale_y)
        vx_r = int(r * self._scale_x)
        vx_b = int(b * self._scale_y)
        region = (vx_l + self.x, vx_t + self.y, vx_r - vx_l, vx_b - vx_t)
        self.result = region
        self._finish(lambda: self.on_select(region, self._region_img))

    def _copy(self):
        if self._region is None:
            self._cancel()
            return
        l, t, r, b = self._region
        vx_l = int(l * self._scale_x)
        vx_t = int(t * self._scale_y)
        vx_r = int(r * self._scale_x)
        vx_b = int(b * self._scale_y)
        region = (vx_l + self.x, vx_t + self.y, vx_r - vx_l, vx_b - vx_t)
        self.result = region
        self._finish(lambda: self.on_copy(region, self._region_img))

    def _save(self):
        if self._region is None:
            self._cancel()
            return
        l, t, r, b = self._region
        vx_l = int(l * self._scale_x)
        vx_t = int(t * self._scale_y)
        vx_r = int(r * self._scale_x)
        vx_b = int(b * self._scale_y)
        region = (vx_l + self.x, vx_t + self.y, vx_r - vx_l, vx_b - vx_t)
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
        self._hide_crosshair()
        self.root.after(0, self._exit)

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
        # 调用 pending callback
        cb = getattr(self, "_pending_callback", None)
        if cb is not None:
            cb()

    def show(self):
        """运行选区窗口。共享根窗口（root 传入）：非阻塞。自建根窗口：阻塞。"""
        if self._owns_root:
            self.root.mainloop()


# 向后兼容别名
RegionSelector = Selector
