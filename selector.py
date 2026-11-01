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
        self._crosshair_label_size = None  # 十字准星标签 bbox 缓存（首次查询后复用）
        self._bg_photo = None    # 背景截图（用于暗化效果）
        self._tb_visible = False  # 工具条是否已显示
        self._tb_size = None      # 工具条尺寸缓存（首次显示时查询）

        if self._owns_root:
            self.root = tk.Tk()
            self.root.title("")
        else:
            # 共享根窗口：选区作为其上的无边框置顶覆盖层
            self.root = tk.Toplevel(root)
            self.root.title("")

        # 动态计算实际屏幕大小（虚拟桌面），用它设置遮罩层窗口尺寸。
        # Win32 GetSystemMetrics 返回的是当前进程 DPI 感知状态下的真实像素，
        # 与 mss 截屏坐标系一致。直接用作窗口尺寸，遮罩层恰好覆盖全部屏幕。
        self._actual_w, self._actual_h = self._get_screen_size()
        # 坐标映射：画布坐标 → mss 虚拟桌面坐标的比例
        # 画布 (0,0)-(_actual_w,_actual_h) 对应虚拟桌面 (x,y)-(x+width,y+height)
        self._scale_x = width / self._actual_w if self._actual_w else 1.0
        self._scale_y = height / self._actual_h if self._actual_h else 1.0

        self.root.geometry(f"{self._actual_w}x{self._actual_h}+{x}+{y}")
        self.root.configure(bg="black")
        # 置顶 + 无边框
        self.root.wm_attributes("-topmost", True)
        self.root.wm_overrideredirect(True)
        # 半透明：让屏幕内容透出来，不定格画面
        self.root.attributes("-alpha", 0.5)
        # Win32 强制扩展窗口到完整虚拟桌面（绕过 Tk 的主显示器钳制）。
        # 必须在 geometry / overrideredirect 之后调用，否则会被 Tk 的
        # configure 流程盖回主显示器尺寸。
        self._force_fullscreen(x, y, self._actual_w, self._actual_h)
        # 再调一次 SetWindowPos 兜底：Tk 后续可能再发 configure 把窗口
        # 钳回主显示器（特别是 -topmost 触发 WS_EX_TOPMOST 后），用
        # 空事件循环 + 再次 SetWindowPos 确保窗口尺寸稳定。
        self.root.update_idletasks()
        self._force_fullscreen(x, y, self._actual_w, self._actual_h)

        # 画布：铺满整个覆盖层，底色为黑色（半透明后呈现暗化效果）
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

        # 深色工具条（初始隐藏，首次拖动出现）
        self._build_toolbar()
        self.tb_win.bind("<Escape>", lambda e: self._cancel())
        self.tb_win.bind("<Return>", lambda e: self._confirm())

        self.root.focus_force()
        self._finished = False

    # ------------------------------------------------------------------ #
    # 屏幕尺寸
    # ------------------------------------------------------------------ #

    def _get_screen_size(self):
        """动态获取实际屏幕大小（虚拟桌面），返回 (width, height)。

        优先用 Win32 GetSystemMetrics（与 mss 截屏坐标系一致），
        失败时回退到 Tk 屏幕尺寸，再失败回退到传入的 width/height。
        """
        if sys.platform == "win32":
            try:
                import ctypes
                user32 = ctypes.windll.user32
                w = user32.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
                h = user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN
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
        """用 Win32 强制把窗口扩展到完整虚拟桌面尺寸。

        Tk 在 Windows 下会把窗口钳制到主显示器，直接 geometry 设更大
        尺寸无效。这里用 SetWindowPos 设精确尺寸，不传 SWP_FRAMECHANGED
        （会触发 Windows 对 WS_POPUP 窗口的钳制），直接 SetWindowPos
        把窗口摆到虚拟桌面 (x, y) 处、尺寸 (width, height)。
        """
        if sys.platform != "win32":
            return
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            hwnd = wintypes.HWND(self.root.winfo_id())
            # HWND_TOPMOST：保持顶层，覆盖全屏
            user32.SetWindowPos(
                hwnd, wintypes.HWND(-1),
                x, y, width, height,
                0,  # 不传 SWP_FRAMECHANGED / SWP_NOZORDER
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

    def _tb_show(self, pos, size):
        """在选区下方显示工具条，pos/size 为画布坐标的选区矩形。

        拖动中每帧调用，必须避免触发 Tcl 布局计算：
        - winfo_exists / winfo_width / winfo_height 只在首次显示时查询一次，
          之后缓存到 _tb_size（工具条尺寸在选区过程中不变）。
        - 后续帧只走 geometry 移动（纯 SetWindowPos，无 layout）。
        """
        if not self._tb_visible:
            if not self.tb_win.winfo_exists():
                return
            self.tb_win.deiconify()
            self.tb_win.update_idletasks()
            self._tb_visible = True
            # 只在首次显示时查询尺寸，之后复用缓存
            self._tb_size = (self.tb_win.winfo_width(), self.tb_win.winfo_height())
        x, y = pos[0], pos[1]
        w, h = size[2], size[3]
        self.tb_label.configure(text=f"{w} x {h}")
        tb_w, tb_h = self._tb_size
        # 默认放选区下方；放不下时放上方（用 Tk 屏幕尺寸判断边界）
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
        # 清空缓存，下次显示时重新查询尺寸
        self._tb_size = None
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
        W, H = self._actual_w, self._actual_h
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
                # 用缓存的 bbox 尺寸（文本宽度变化时近似足够），避免每帧
                # bbox() 触发 Tcl 布局计算。文本宽度变化时误差可接受。
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
        """隐藏十字准星。"""
        for attr in ("_crosshair_h", "_crosshair_v", "_crosshair_label", "_crosshair_label_bg"):
            item = getattr(self, attr, None)
            if item is not None:
                try:
                    self.canvas.delete(item)
                except tk.TclError:
                    pass
                setattr(self, attr, None)
        # 清空 bbox 缓存，下次显示时重新查询
        self._crosshair_label_size = None

    # ------------------------------------------------------------------ #
    # 渲染
    # ------------------------------------------------------------------ #

    def _render_all(self):
        """重绘动态元素（遮罩/选区/手柄/标签/工具条）。

        使用传入的 width/height（主显示器 mss 尺寸）作为画布范围，
        鼠标事件坐标在 [0, width-1] x [0, height-1] 范围内。
        使用 coords() 更新已有元素，避免 delete+create 的闪烁。
        """
        l, t, r, b = self._region if self._region else (None, None, None, None)
        if self._mode != "adjust" or l is None:
            return

        # 画布范围 = Tk 屏幕尺寸（不是 mss 虚拟桌面尺寸）
        W, H = self._actual_w, self._actual_h

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
                self._items["label_bg_size"] = (x2 - x1, y2 - y1)
                self.canvas.tag_raise(tid)
            self._items["label"] = tid
        else:
            tid = self._items["label"]
            self.canvas.coords(tid, tx, ty)
            self.canvas.itemconfig(tid, text=text)
            if "label_bg" in self._items:
                # 用缓存的 bbox 尺寸，避免每帧 bbox() 触发 Tcl 布局计算
                size = self._items.get("label_bg_size")
                if size:
                    tw, th = size
                    self.canvas.coords(
                        self._items["label_bg"],
                        tx - 4, ty - 2, tx + tw + 4, ty + th + 2,
                    )

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
            # 手柄中心相对选区角落是固定偏移（nw=0,0 / n=mid,0 / ...），
            # 用 dx/dy 移动避免重建 _handle_coords 字典（虽然开销小，
            # 但 8 次 dict lookup + 8 次 coords 调用可优化）。
            s = self.HANDLE_SIZE
            h = self._items["handles"]
            mid_x = (l + r) // 2
            mid_y = (t + b) // 2
            self.canvas.coords(h["nw"], l - s, t - s, l + s, t + s)
            self.canvas.coords(h["n"], mid_x - s, t - s, mid_x + s, t + s)
            self.canvas.coords(h["ne"], r - s, t - s, r + s, t + s)
            self.canvas.coords(h["e"], r - s, mid_y - s, r + s, mid_y + s)
            self.canvas.coords(h["se"], r - s, b - s, r + s, b + s)
            self.canvas.coords(h["s"], mid_x - s, b - s, mid_x + s, b + s)
            self.canvas.coords(h["sw"], l - s, b - s, l + s, b + s)
            self.canvas.coords(h["w"], l - s, mid_y - s, l + s, mid_y + s)

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
        """把 (l, t, r, b) 归一化并夹到画布内（Tk 屏幕尺寸），返回 (l, t, r, b)；无效返回 None。"""
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
            # 创建 1x1 的最小框（以按下点为左上角），夹到 Tk 屏幕范围内
            x, y = event.x, event.y
            W, H = self._actual_w, self._actual_h
            x = max(0, min(x, W - 1))
            y = max(0, min(y, H - 1))
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
            # 鼠标事件坐标范围是 [0, width-1]，拖到屏幕边缘时 snap 到完整边缘，
            # 否则选区永远差 1px 选不满。
            # 起点和终点任一贴边都要 snap（覆盖正向/反向拖拽）。
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
        # 把 Tk 坐标（画布坐标）映射到 mss 虚拟桌面坐标
        # Tk (0,0)-(_tk_w,_tk_h) 对应虚拟桌面 (x,y)-(x+width,y+height)
        vx_l = int(l * self._scale_x)
        vx_t = int(t * self._scale_y)
        vx_r = int(r * self._scale_x)
        vx_b = int(b * self._scale_y)
        self._finish((vx_l + self.x, vx_t + self.y, vx_r - vx_l, vx_b - vx_t))

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
