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


class RegionSelector:
    """全屏透明覆盖层，仿飞书截图交互：拖拽框选 → 出现 8 个调整手柄 →
    可拖动/缩放选区 → 确认（Enter / 双击）或取消（Esc / 右键）。

    x, y: 窗口左上角在虚拟桌面中的坐标（使窗口 (0,0) 对齐虚拟桌面 (0,0)）
    width, height: 虚拟桌面尺寸
    on_select: 选区结束回调，参数为 (left, top, width, height) 或 None（取消）
    root: 可选的共享 Tk 根窗口。传入时选区作为其 Toplevel 打开（非阻塞）；
          不传则自建根窗口并阻塞运行（show）。
    """

    MIN_SIZE = 5
    HANDLE_SIZE = 12  # 手柄边长（像素）—— 加大到 12，更易抓
    MARGIN = 8        # 手柄超出选区边缘的距离 —— 加大到 8，热区更大

    # 8 个手柄的标识
    HANDLES = ("nw", "n", "ne", "e", "se", "s", "sw", "w")

    # 各手柄的窗口光标（拖动方向提示）
    _CURSORS = {"nw": "fleur", "n": "sb_up_arrow", "ne": "crosshair",
                "e": "sb_right_arrow", "se": "fleur", "s": "sb_down_arrow",
                "sw": "crosshair", "w": "sb_left_arrow", "move": "fleur"}

    def __init__(self, x, y, width, height, on_select, root=None):
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.on_select = on_select
        self.result = None
        self._owns_root = root is None
        self._mode = "select"
        self._items = {}          # {"rect": id, "handles": {...}, "label": id}
        self._label_pos = None
        self._region = None
        self._drag_op = None
        self._drag_start = None
        self._drag_region = None
        self._crosshair_h = None  # 十字准星水平线
        self._crosshair_v = None  # 十字准星垂直线
        self._crosshair_label = None  # 十字准星尺寸标签
        self._crosshair_label_bg = None

        if self._owns_root:
            self.root = tk.Tk()
            self.root.title("")
        else:
            # 共享根窗口：选区作为其上的无边框置顶覆盖层
            self.root = tk.Toplevel(root)
            self.root.title("")
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.attributes("-fullscreen", True)
        self.root.configure(bg="black")
        # 置顶 + 无边框
        self.root.wm_attributes("-topmost", True)
        self.root.wm_overrideredirect(True)
        self.root.attributes("-alpha", 1.0)

        # 画布：铺满整个覆盖层，底色为黑色（配合 alpha 遮罩实现暗化）
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

        # 截图背景图（全屏抓取），用于"框外暗化、框内原图"的遮罩效果
        self._photo = None
        self._init_background()

        # 深色工具条（初始隐藏，首次拖动出现）
        self._build_toolbar()
        self.tb_win.bind("<Escape>", lambda e: self._cancel())
        self.tb_win.bind("<Return>", lambda e: self._confirm())

        self.root.focus_force()
        self._finished = False

    # ------------------------------------------------------------------ #
    # 背景（遮罩）
    # ------------------------------------------------------------------ #

    def _init_background(self):
        """后台抓取全屏截图并铺到画布。"""
        import threading

        def work():
            try:
                from capture import capture_fullscreen
                img = capture_fullscreen()
            except Exception:
                img = None
            self.root.after(0, lambda: self._bg_ready(img))

        threading.Thread(target=work, daemon=True).start()

    def _bg_ready(self, img):
        """背景截图就绪：绘制一次全屏图，后续只更新动态元素。"""
        if not self.canvas.winfo_exists():
            return
        if img is not None:
            from PIL import ImageTk
            self._photo = ImageTk.PhotoImage(img)
            self.canvas.create_image(0, 0, anchor="nw", image=self._photo)
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

        # 左侧操作：确认 / 取消
        self._tb_btn(bar, "✓ 确认", lambda: self._confirm())
        sep = tk.Frame(bar, width=1, height=18, bg=TB_SEP)
        sep.pack(side="left", padx=5)
        self._tb_btn(bar, "✕ 取消", lambda: self._cancel())

        # 右侧尺寸标签
        self.tb_label = tk.Label(bar, text="", fg=LABEL_FG, bg=TB_BG,
                                 font=TB_FONT)
        self.tb_label.pack(side="right", padx=(4, 8))

    def _tb_btn(self, parent, text, command):
        b = tk.Label(parent, text=text, fg=TB_FG, bg=TB_BG, font=TB_FONT,
                     padx=6, pady=2, cursor="hand2")
        b.pack(side="left", padx=1)
        b.bind("<Enter>", lambda e, bb=b: bb.configure(bg=TB_HOVER, fg=TB_HOVER_FG))
        b.bind("<Leave>", lambda e, bb=b: bb.configure(bg=TB_BG, fg=TB_FG))
        b.bind("<Button-1>", lambda e, c=command: c())
        return b

    _tb_visible = False

    def _tb_show(self, pos, size):
        """在选区下方显示工具条，pos/size 为画布坐标的选区矩形。"""
        if not self.tb_win.winfo_exists():
            return
        x, y = pos[0], pos[1]
        w, h = size[2], size[3]
        if not self._tb_visible:
            self.tb_win.deiconify()
            self.tb_win.update_idletasks()
            self._tb_visible = True
        self.tb_label.configure(text=f"{w} x {h}")
        tb_w = self.tb_win.winfo_width()
        tb_h = self.tb_win.winfo_height()
        # 默认放选区下方；放不下时放上方
        ty = y + h + 4
        if ty + tb_h > self.height:
            ty = y - tb_h - 4
        if ty < 0:
            ty = y + h + 4
        tx = max(0, min(x, self.width - tb_w)) if tb_w else x
        self.tb_win.geometry(f"+{self.x + tx}+{self.y + ty}")

    def _tb_hide(self):
        self._tb_visible = False
        if self.tb_win is not None and self.tb_win.winfo_exists():
            try:
                self.tb_win.withdraw()
            except tk.TclError:
                pass

    # ------------------------------------------------------------------ #
    # 十字准星
    # ------------------------------------------------------------------ #

    def _show_crosshair(self, x, y, w, h):
        """显示十字准星 + 实时尺寸标签。x, y 为鼠标位置，w, h 为当前选区尺寸。"""
        W, H = self.width, self.height
        # 水平线
        if self._crosshair_h is None:
            self._crosshair_h = self.canvas.create_line(
                0, y, W, y, fill=CROSSHAIR_COLOR, width=CROSSHAIR_WIDTH)
        else:
            self.canvas.coords(self._crosshair_h, 0, y, W, y)
        # 垂直线
        if self._crosshair_v is None:
            self._crosshair_v = self.canvas.create_line(
                x, 0, x, H, fill=CROSSHAIR_COLOR, width=CROSSHAIR_WIDTH)
        else:
            self.canvas.coords(self._crosshair_v, x, 0, x, H)
        # 尺寸标签（鼠标右下角）
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
                try:
                    bbox = tuple(self.canvas.bbox(self._crosshair_label))
                except (TypeError, ValueError, tk.TclError):
                    bbox = None
                if bbox:
                    x1, y1, x2, y2 = bbox
                    self.canvas.coords(self._crosshair_label_bg, x1-4, y1-2, x2+4, y2+2)

    def _hide_crosshair(self):
        """隐藏十字准星。"""
        for attr in ("_crosshair_h", "_crosshair_v", "_crosshair_label", "_crosshair_label_bg"):
            item = getattr(self, attr, None)
            if item is not None:
                try:
                    self.canvas.delete(item)
                except tk.TclError:
                    pass
                setattr(self, attr, None)

    # ------------------------------------------------------------------ #
    # 渲染
    # ------------------------------------------------------------------ #

    def _render_all(self):
        """重绘动态元素（遮罩/选区/手柄/标签/工具条）。

        全屏背景图只在 _bg_ready 时绘制一次，后续拖动/缩放只更新动态元素。
        使用 coords() 更新已有元素，避免 delete+create 的闪烁。
        """
        l, t, r, b = self._region if self._region else (None, None, None, None)
        if self._mode != "adjust" or l is None:
            return

        W, H = self.width, self.height

        # 确保 4 个遮罩存在
        if "m1" not in self._items:
            self._items["m1"] = self.canvas.create_rectangle(0, 0, 0, 0, fill="black", stipple="gray50", width=0)
            self._items["m2"] = self.canvas.create_rectangle(0, 0, 0, 0, fill="black", stipple="gray50", width=0)
            self._items["m3"] = self.canvas.create_rectangle(0, 0, 0, 0, fill="black", stipple="gray50", width=0)
            self._items["m4"] = self.canvas.create_rectangle(0, 0, 0, 0, fill="black", stipple="gray50", width=0)
            self._items["rect"] = self.canvas.create_rectangle(0, 0, 0, 0, outline="white", width=2)
        # 更新遮罩坐标
        self.canvas.coords(self._items["m1"], 0, 0, r, b)
        self.canvas.coords(self._items["m2"], l, 0, W, t)
        self.canvas.coords(self._items["m3"], 0, t, l, H)
        self.canvas.coords(self._items["m4"], r, t, W, H)
        # 更新选区边框
        self.canvas.coords(self._items["rect"], l, t, r, b)

        # 尺寸标签
        text = f"{r - l} x {b - t}"
        tx = l
        ty = t - 8
        if t - 24 < 0:
            ty = b + 8
        if "label" not in self._items:
            tid = self.canvas.create_text(
                tx, ty, text=text, fill=LABEL_FG, anchor="nw",
                font=("Microsoft YaHei UI", 9))
            self.canvas.tag_raise(tid)
            try:
                bbox = tuple(self.canvas.bbox(tid))
            except (TypeError, ValueError, tk.TclError):
                bbox = None
            if bbox:
                x1, y1, x2, y2 = bbox
                self._items["label_bg"] = self.canvas.create_rectangle(
                    x1 - 4, y1 - 2, x2 + 4, y2 + 2,
                    fill=LABEL_BG, outline="", width=0)
                self.canvas.tag_raise(tid)
            self._items["label"] = tid
        else:
            tid = self._items["label"]
            self.canvas.coords(tid, tx, ty)
            self.canvas.itemconfig(tid, text=text)
            if "label_bg" in self._items:
                try:
                    bbox = tuple(self.canvas.bbox(tid))
                except (TypeError, ValueError, tk.TclError):
                    bbox = None
                if bbox:
                    x1, y1, x2, y2 = bbox
                    self.canvas.coords(self._items["label_bg"], x1-4, y1-2, x2+4, y2+2)

        # 手柄
        if "handles" not in self._items:
            handles = {}
            for name, (hx, hy) in self._handle_coords(l, t, r, b).items():
                s = self.HANDLE_SIZE
                handles[name] = self.canvas.create_rectangle(
                    hx - s, hy - s, hx - s + 2 * s, hy - s + 2 * s,
                    fill="#ffffff", outline=TB_BG, width=1)
            self._items["handles"] = handles
        else:
            for name, (hx, hy) in self._handle_coords(l, t, r, b).items():
                s = self.HANDLE_SIZE
                self.canvas.coords(
                    self._items["handles"][name],
                    hx - s, hy - s, hx - s + 2 * s, hy - s + 2 * s)

        # 显示工具条
        self._tb_show((l, t, r, b), (l, t, r - l, b - t))

    def _handle_coords(self, l, t, r, b):
        """返回 8 个手柄的中心坐标 {name: (x, y)}。"""
        return {
            "nw": (l, t), "n": ((l + r) // 2, t), "ne": (r, t),
            "e": (r, (t + b) // 2), "se": (r, b),
            "s": ((l + r) // 2, b), "sw": (l, b), "w": (l, (t + b) // 2),
        }

    # ------------------------------------------------------------------ #
    # 坐标 / 区域计算
    # ------------------------------------------------------------------ #

    def _to_virtual(self, wx, wy):
        """画布内坐标 → 虚拟桌面坐标。"""
        return (wx + self.x, wy + self.y)

    def _calc_region(self, wx1, wy1, wx2, wy2):
        """由两个画布坐标计算归一化选区 (left, top, w, h)；太小返回 None。"""
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
        """把 (l, t, r, b) 归一化并夹到画布内，返回 (l, t, r, b)；无效返回 None。"""
        l, t, r, b = region
        l, r = min(l, r), max(l, r)
        t, b = min(t, b), max(t, b)
        if r < 0 or b < 0 or l >= self.width or t >= self.height:
            return None
        l = max(0, min(l, self.width))
        t = max(0, min(t, self.height))
        r = max(0, min(r, self.width))
        b = max(0, min(b, self.height))
        if r - l < self.MIN_SIZE or b - t < self.MIN_SIZE:
            return None
        return (l, t, r, b)

    # ------------------------------------------------------------------ #
    # 鼠标事件
    # ------------------------------------------------------------------ #

    def _hit_test(self, x, y):
        """判断点 (x, y) 落在哪个手柄/区域，返回手柄名或 None。"""
        if self._mode != "adjust" or self._region is None:
            return None
        l, t, r, b = self._region
        s = self.HANDLE_SIZE
        m = self.MARGIN
        for name, (hx, hy) in self._handle_coords(l, t, r, b).items():
            if abs(x - hx) <= s + m and abs(y - hy) <= s + m:
                return name
        return None

    def _on_press(self, event):
        if self._mode == "select":
            # 按下立刻创建最小框并进入调整模式（仿飞书：按下就有框）
            self.start = (event.x, event.y)
            self._mode = "adjust"
            # 创建 1x1 的最小框（以按下点为左上角）
            x, y = event.x, event.y
            x = max(0, min(x, self.width - 1))
            y = max(0, min(y, self.height - 1))
            self._region = (x, y, x + 1, y + 1)
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
        # 点在手柄之外但在选区内部 → 移动
        l, t, r, b = self._region
        if l <= event.x <= r and t <= event.y <= b:
            self._drag_op = ("move", "move")
            self._drag_start = (event.x, event.y)
            self._drag_region = self._region
            self.canvas.configure(cursor="sizing")
            return

    def _on_drag(self, event):
        if self._mode == "adjust" and self._drag_op is None and getattr(self, "start", None):
            # 从 select 阶段进入 adjust 后的第一次拖动：更新框的大小
            # 框的左上角固定在 start，右下角跟手
            sx, sy = self.start
            x1, y1 = min(sx, event.x), min(sy, event.y)
            x2, y2 = max(sx, event.x), max(sy, event.y)
            w = x2 - x1
            h = y2 - y1
            # 如果拖得太小（< 5px），保持最小框
            if w < self.MIN_SIZE and h < self.MIN_SIZE:
                # 不更新，保持当前最小框
                return
            self._region = self._normalize((x1, y1, x2, y2))
            # 显示十字准星
            if self._region:
                l, t, r, b = self._region
                self._show_crosshair(event.x, event.y, r - l, b - t)
            self._render_all()
            return
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
            self._render_all()
            return
        # select 模式下不应该有 drag（已经在 press 时切到 adjust 了）
        if self._mode == "select":
            return

    def _on_release(self, event):
        # 清理拖动状态
        self.start = None
        self._drag_op = None
        self._drag_start = None
        self._drag_region = None
        self.canvas.configure(cursor="crosshair")
        self._hide_crosshair()
        if self._region is None:
            # 调整中被缩到无效 → 回退到纯选区模式
            self._mode = "select"
            self._render_all()
        # 否则保持 adjust 模式，等待确认

    # ------------------------------------------------------------------ #
    # 确认 / 取消
    # ------------------------------------------------------------------ #

    def _confirm(self):
        if self._region is None:
            self._cancel()
            return
        l, t, r, b = self._region
        self._finish((l + self.x, t + self.y, r - l, b - t))

    def _cancel(self):
        self._finish(None)

    def _finish(self, result):
        """结束选区：记录结果并关闭选区窗口，再调 on_select 回调。"""
        if self._finished:
            return
        self._finished = True
        self.result = result
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
        # 清理画布上的所有动态元素
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
        self.on_select(self.result)

    def show(self):
        """运行选区窗口。

        共享根窗口（root 传入）：非阻塞，直接返回，on_select 在后续事件中触发。
        自建根窗口（root 未传）：阻塞运行 mainloop，直到选区结束。
        """
        if self._owns_root:
            self.root.mainloop()
