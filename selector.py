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
    HANDLE_SIZE = 8   # 手柄边长（像素）
    MARGIN = 4        # 手柄超出选区边缘的距离

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
        # 在 overrideredirect 之后再设 -alpha：
        # -alpha 会给窗口加 WS_EX_LAYERED，而 overrideredirect 触发的
        # SetWindowPos 若不带 SWP_NOOWNERZORDER 会重新分配窗口父级，
        # 导致覆盖层不再是顶层全屏窗口、鼠标事件丢失。
        # 之后重设 -alpha 可确保扩展样式最终正确。
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
        """后台抓取全屏截图并铺到画布。

        抓图放到工作线程，完成后调度回主线程渲染，避免大窗口卡顿。
        """
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
        """在选区上方/下方显示工具条，pos/size 为画布坐标的选区矩形。

        只在工具条不可见时 deiconify 一次，后续只移动位置 + 更新文字，
        避免每次拖动都 deiconify/update_idletasks 导致卡死。
        """
        if not self.tb_win.winfo_exists():
            return
        x, y = pos[0], pos[1]
        w, h = size[2], size[3]
        # 只在首次显示时 deiconify + 测量
        if not self._tb_visible:
            self.tb_win.deiconify()
            self.tb_win.update_idletasks()
            self._tb_visible = True
        self.tb_label.configure(text=f"{w} x {h}")
        tb_w = self.tb_win.winfo_width()
        tb_h = self.tb_win.winfo_height()
        # 默认放选区上方；放不下时放下方；左右贴边
        ty = y - tb_h - 4
        if ty < 0:
            ty = y + h + 4
        if ty + tb_h > self.height:
            ty = max(0, self.height - tb_h)
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
    # 渲染
    # ------------------------------------------------------------------ #

    def _render_all(self):
        """重绘动态元素（遮罩/选区/手柄/标签/工具条）。

        全屏背景图只在 _bg_ready 时绘制一次，后续拖动/缩放只更新动态元素，
        避免每次鼠标事件都重绘全屏图导致卡死。
        """
        # 清除上一次的动态元素
        for item in self._items.values():
            if isinstance(item, dict):
                for child in item.values():
                    self.canvas.delete(child)
            elif item:
                self.canvas.delete(item)
        self._items.clear()

        if self._mode != "adjust" or self._region is None:
            return

        l, t, r, b = self._region
        # 暗化遮罩：画布 4 个角落的黑色矩形（框外暗化，框内保留原图）
        W, H = self.width, self.height
        mask = lambda x1, y1, x2, y2: self.canvas.create_rectangle(
            x1, y1, x2, y2, fill="black", stipple="gray50", width=0)
        self._items["m1"] = mask(0, 0, r, b)        # 左上
        self._items["m2"] = mask(l, 0, W, t)        # 右上
        self._items["m3"] = mask(0, t, l, H)        # 左下
        self._items["m4"] = mask(r, t, W, H)        # 右下
        # 选区边框
        self._items["rect"] = self.canvas.create_rectangle(
            l, t, r, b, outline="white", width=2)
        # 尺寸标签：优先放选区上方，越界则放下方
        text = f"{r - l} x {b - t}"
        lx = l
        ly = t - 8
        if t - 24 < 0:
            ly = b + 8
        self._items["label"] = self.canvas.create_text(
            lx, ly, text=text, fill=LABEL_FG, bg=LABEL_BG, anchor="nw",
            font=("Microsoft YaHei UI", 9))
        # 手柄
        handles = {}
        for name, (hx, hy) in self._handle_coords(l, t, r, b).items():
            s = self.HANDLE_SIZE
            m = self.MARGIN
            handles[name] = self.canvas.create_rectangle(
                hx - s, hy - s, hx - s + 2 * s, hy - s + 2 * s,
                fill="#ffffff", outline=TB_BG, width=1)
        self._items["handles"] = handles
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
    # 坐标 / 区域计算（保持与原 API 兼容，供测试使用）
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
            self.start = (event.x, event.y)
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
            self.canvas.configure(cursor="move")
            return

    def _on_drag(self, event):
        if self._mode == "select":
            if not getattr(self, "start", None):
                return
            # 第一次拖动就切换到调整模式
            self._mode = "adjust"
            r = self._calc_region(self.start[0], self.start[1], event.x, event.y)
            if r:
                self._region = self._normalize((r[0], r[1], r[0] + r[2], r[1] + r[3]))
            else:
                self._region = None
            self._render_all()
            return
        if self._drag_op is None or self._drag_start is None:
            return
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
        self._render_all()

    def _on_release(self, event):
        if self._mode == "select":
            # 点击太小：不进入调整，直接忽略
            self.start = None
            self._drag_op = None
            return
        self._drag_op = None
        self._drag_start = None
        self._drag_region = None
        self.canvas.configure(cursor="crosshair")
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
        """结束选区：记录结果并关闭选区窗口，再调 on_select 回调。

        用 after(0) 推迟销毁+回调，避免在事件处理里同步销毁窗口导致事件循环
        异常（遮罩卡住 / Tcl 解释器被破坏）。共享根窗口模式下不退出 mainloop。
        """
        if self._finished:
            return
        self._finished = True
        self.result = result
        self._tb_hide()
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
        self.on_select(self.result)

    def show(self):
        """运行选区窗口。

        共享根窗口（root 传入）：非阻塞，直接返回，on_select 在后续事件中触发。
        自建根窗口（root 未传）：阻塞运行 mainloop，直到选区结束。
        """
        if self._owns_root:
            self.root.mainloop()
