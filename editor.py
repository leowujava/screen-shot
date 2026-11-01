import sys
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog, colorchooser
from PIL import Image, ImageDraw, ImageTk

from capture import copy_to_clipboard
from drawing import (
    mosaic, UndoStack,
    draw_arrow, draw_rect, draw_ellipse, draw_brush, draw_text,
)

import json
import os

# OCR 引擎（懒加载，未安装时 OCR 功能会提示而不影响其余功能）
# 使用 rapidocr（PP-OCRv6，中英混合识别），而非仅支持中文的 rapidocr-onnxruntime
_OCR_ENGINE = None
_OCR_ENGINE_READY = False

# 编辑器设置文件（位于用户目录）
_SETTINGS_PATH = os.path.join(os.path.expanduser("~"), ".screen_shot_settings.json")
# 旧文件名（项目改名前），仅用于兼容读取
_LEGACY_SETTINGS_PATH = os.path.join(os.path.expanduser("~"), ".screem_snap_settings.json")


def _load_settings():
    """读取持久化设置，失败时返回空字典。"""
    for path in (_SETTINGS_PATH, _LEGACY_SETTINGS_PATH):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            continue
    return {}


def _save_settings(settings):
    """保存设置，失败时静默忽略。"""
    try:
        with open(_SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False)
    except Exception:
        pass


def _is_hex_color(value):
    """判断是否为合法的十六进制颜色（#rgb 或 #rrggbb）。"""
    if not isinstance(value, str):
        return False
    v = value.strip().lstrip("#")
    if len(v) not in (3, 6):
        return False
    try:
        int(v, 16)
        return True
    except ValueError:
        return False


def _get_ocr_engine():
    """懒加载 OCR 引擎（首次调用时初始化）。失败返回 None。

    优先使用 rapidocr（PP-OCRv6，中英混合）；回退 rapidocr-onnxruntime（仅中文）。
    """
    global _OCR_ENGINE, _OCR_ENGINE_READY
    if _OCR_ENGINE_READY:
        return _OCR_ENGINE
    try:
        from rapidocr import RapidOCR
        _OCR_ENGINE = RapidOCR()
        _OCR_ENGINE_READY = True
        return _OCR_ENGINE
    except Exception:
        try:
            from rapidocr_onnxruntime import RapidOCR
            _OCR_ENGINE = RapidOCR()
            _OCR_ENGINE_READY = True
            return _OCR_ENGINE
        except Exception:
            return None


def ocr_result_to_text(result):
    """把 OCR 结果拼成按阅读顺序的文本（先上后下、先左后右，换行连接）。

    兼容两种返回格式：
    - rapidocr (v3) 的 RapidOCROutput：.boxes (n×4×2 ndarray) 与 .txts 一一对应
    - rapidocr-onnxruntime 的 list：[[box, text, conf], ...]
    result 为 None / 空时返回空字符串。
    """
    if result is None:
        return ""

    # 格式一：rapidocr v3 的 RapidOCROutput（有 .boxes / .txts 属性）
    boxes = getattr(result, "boxes", None)
    txts = getattr(result, "txts", None)
    if boxes is not None and txts is not None:
        if len(txts) == 0:
            return ""
        idx = sorted(range(len(txts)),
                     key=lambda i: (round(boxes[i][0][1] / 8), boxes[i][0][0]))
        return "\n".join(txts[i] for i in idx)

    # 格式二：rapidocr-onnxruntime 的 list [[box, text, conf], ...]
    if isinstance(result, (list, tuple)):
        if len(result) == 0:
            return ""
        idx = sorted(range(len(result)),
                     key=lambda i: (round(result[i][0][0][1] / 8), result[i][0][0][0]))
        return "\n".join(result[i][1] for i in idx)

    return ""


class Editor:
    """截图编辑窗口。

    image: PIL Image 待编辑图
    on_close: 关闭回调
    """

    TOOLS = ("brush", "rect", "ellipse", "arrow", "mosaic", "text", "crop")
    TOOL_LABELS = {"brush": "画笔", "rect": "矩形", "ellipse": "圆形",
                   "arrow": "箭头", "mosaic": "打码", "text": "文字",
                   "crop": "裁剪"}
    TOOL_ICONS = {"brush": "✏", "rect": "▭", "ellipse": "◯", "arrow": "↗",
                  "mosaic": "▦", "text": "T", "crop": "✂"}

    # 主题色
    BG = "#f3f5f7"          # 工具栏背景
    BG_DARK = "#2b2f36"     # 选中工具按钮背景
    FG = "#2b2f36"          # 文字颜色
    FG_SELECTED = "#ffffff" # 选中工具按钮文字
    BTN_BG = "#ffffff"      # 普通按钮背景
    BTN_BORDER = "#d7dbe0"  # 按钮边框
    BTN_HOVER = "#e9eef2"   # 悬停
    ACCENT = "#4a90e2"      # 强调色
    SEPARATOR = "#d7dbe0"
    CANVAS_BG_DEFAULT = "#1f2228"  # 画布（图像周围）默认背景

    def __init__(self, image, on_close, root=None):
        """
        image: PIL Image 待编辑图
        on_close: 关闭回调
        root: 可选的共享 Tk 根窗口。传入时编辑器作为其 Toplevel 打开（非阻塞）；
              不传则自建根窗口并阻塞运行（show）。
        """
        self._owns_root = root is None
        self._shared_root = root
        self.image = image.copy()
        self.original = image.copy()
        self.on_close = on_close
        self.undo = UndoStack(maxlen=10)
        self.tool = "brush"
        self.color = "#ff0000"
        # 画布背景色（可设置，持久化到设置文件）
        self.canvas_bg = _load_settings().get("canvas_bg", self.CANVAS_BG_DEFAULT)
        # 校验颜色字符串合法性，非法则回退默认
        if not _is_hex_color(self.canvas_bg):
            self.canvas_bg = self.CANVAS_BG_DEFAULT
        self.width = 3
        self.drawing = False
        self.draw_start = None
        self._brush_last = None
        self._preview_id = None
        self._tool_btns = {}
        self._off_x = 0
        self._off_y = 0

        self._build_ui()
        self._render()

    # 工具栏所需的最小宽度（保证所有按钮完整显示）
    MIN_W = 680
    MIN_H = 300

    # 图像四周留白（像素）
    PAD = 16

    def _build_ui(self):
        if self._owns_root:
            self.root = tk.Tk()
            self.root.title("截图编辑")
        else:
            self.root = tk.Toplevel(self._shared_root)
            self.root.title("截图编辑")

        w, h = self.image.size
        # 窗口尺寸 = 图像尺寸 + 四周留白（图像居中，不贴边）
        self.win_w = w
        self.win_h = h
        pad2 = 2 * self.PAD
        self.root.geometry(f"{w + pad2}x{h + pad2}+100+100")
        self.root.configure(bg=self.CANVAS_BG_DEFAULT)

        self._style_flat_buttons()

        # 画布：铺满整个窗口，图像居中绘制，四周留出背景作为留白
        self.canvas = tk.Canvas(self.root, width=w + pad2, height=h + pad2,
                                bg=self.canvas_bg, highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Configure>", lambda e: self._render())
        self.root.bind("<Control-z>", lambda e: self._undo())
        self.root.bind("<Control-s>", lambda e: self._save())
        self.root.bind("<Control-c>", lambda e: self._copy())

        # 悬浮工具栏（最后创建，保证在最上层）
        self._build_toolbar()

        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self._photo = None

    def _build_toolbar(self):
        """构建悬浮工具栏：无边框置顶小窗口，叠加在图像上方，可拖动。

        工具栏不占用画布空间；通过 -yoffset 让它始终位于图像顶部。
        """
        TB_BG = "#2b2f36"
        TB_FG = "#f5f6f8"
        TB_HOVER = "#4a90e2"
        TB_HOVER_FG = "#ffffff"
        TB_BTN_BG = "#3a3f47"
        TB_BTN_BORDER = "#4a5160"
        FONT = ("Microsoft YaHei UI", 9)

        # 工具栏作为独立顶层窗口（parent=None），不继承编辑框根窗口的
        # -topmost 属性，从而不会始终浮在所有窗口之上。
        self.toolbar = tk.Toplevel()
        self.toolbar.wm_overrideredirect(True)      # 无边框
        self.toolbar.configure(bg=TB_BG, bd=1, relief="solid")

        bar = tk.Frame(self.toolbar, bg=TB_BG)
        bar.pack(fill="x", padx=6, pady=5)

        # --- 工具按钮组 ---
        group = tk.Frame(bar, bg=TB_BG)
        group.pack(side="left")
        self.tool_var = tk.StringVar(value="brush")
        for i, name in enumerate(self.TOOLS):
            btn = tk.Label(
                group, text=f"{self.TOOL_ICONS[name]}",
                fg=TB_FG, bg=TB_BTN_BG,
                font=("Segoe UI", 11), padx=7, pady=3,
                cursor="hand2", relief="flat", bd=0,
            )
            btn.grid(row=0, column=i, padx=2)
            btn.bind("<Enter>", lambda e, b=btn: b.configure(bg=TB_HOVER, fg=TB_HOVER_FG))
            btn.bind("<Leave>", lambda e, b=btn, n=name: self._restool_hover_tb(n, b))
            btn.bind("<Button-1>", lambda e, n=name: self._select_tool(n))
            self._tool_btns[name] = btn
        self._refresh_tool_btns()

        # 分隔
        tk.Frame(bar, width=1, height=22, bg=TB_BTN_BORDER).pack(side="left", padx=6)

        # --- 颜色 ---
        self.color_btn = tk.Button(
            bar, text="", fg=TB_FG, bg=self.color,
            relief="flat", bd=1, width=4, height=1, cursor="hand2",
            activebackground=self.color, command=self._pick_color,
            highlightthickness=1, highlightbackground=TB_BTN_BORDER,
        )
        self.color_btn.pack(side="left", ipady=4, padx=2)

        # --- 粗细 ---
        self.width_var = tk.IntVar(value=self.width)
        spin = tk.Spinbox(
            bar, from_=1, to=30, width=3, textvariable=self.width_var,
            font=FONT, relief="solid", bd=1,
            highlightthickness=1, highlightbackground=TB_BTN_BORDER,
            bg=TB_BTN_BG, fg=TB_FG,
        )
        spin.pack(side="left", padx=4, ipady=1)

        # --- 背景色 ---
        self.canvas_bg_btn = tk.Button(
            bar, text="", fg=TB_FG, bg=self.canvas_bg,
            relief="flat", bd=1, width=4, height=1, cursor="hand2",
            activebackground=self.canvas_bg, command=self._pick_canvas_bg,
            highlightthickness=1, highlightbackground=TB_BTN_BORDER,
        )
        self.canvas_bg_btn.pack(side="left", ipady=4, padx=2)

        # 分隔
        tk.Frame(bar, width=1, height=22, bg=TB_BTN_BORDER).pack(side="left", padx=6)

        # --- 操作按钮 ---
        self._tb_action_btn(bar, "↩", "撤销", self._undo)
        self._tb_action_btn(bar, "⧉", "复制", self._copy)
        self._tb_action_btn(bar, "🔍", "识别", self._ocr)
        self._tb_action_btn(bar, "💾", "保存", self._save, primary=True)
        self._tb_action_btn(bar, "✕", "关闭", self._close)

        # 自由移动：按住左键拖动时跟随光标
        self.toolbar.bind("<ButtonPress-1>", self._tb_start_follow)
        self.toolbar.bind("<B1-Motion>", self._tb_do_follow)
        self.toolbar.bind("<ButtonRelease-1>", self._tb_stop_follow)
        self.toolbar.configure(cursor="fleur")
        self._tb_follow = False
        # 上一次的编辑框几何（用于判断是否需要跟随重排）
        self._tb_last_geom = None

        # 初始位置：编辑框上方左对齐（拖动后改为相对偏移跟随）
        self._tb_docked = True
        self._tb_off_x = 0
        self._tb_off_y = 0
        self._position_toolbar()

        # 跟随编辑框：窗口移动/缩放/隐藏时工具栏同步
        self.root.bind("<Configure>", self._tb_sync_with_window)
        self.root.bind("<Unmap>", self._tb_sync_with_window)
        self.root.bind("<Map>", self._tb_sync_with_window)
        # 安全网：Windows 窗口管理器移动窗口时 <Configure> 可能不触发，
        # 用轮询兜底，确保工具栏始终跟随编辑框
        self._tb_poll()
        # 工具栏是独立顶层窗口，编辑框被销毁（如任务栏关闭）时一并销毁
        self.root.protocol("WM_DELETE_WINDOW", self._on_root_delete)

        # 记录工具栏宽度，作为编辑框最小宽度（工具栏内容固定，只需测一次）
        self._tb_min_w = 0
        self.root.after_idle(self._measure_toolbar_width)

    def _tb_action_btn(self, parent, icon, label, command, primary=False):
        """工具栏操作按钮（图标 + 文字）。"""
        TB_BG = "#2b2f36"
        TB_BTN_BG = "#3a3f47"
        bg = "#4a90e2" if primary else TB_BTN_BG
        fg = "#ffffff" if primary else "#f5f6f8"
        b = tk.Label(
            parent, text=f"{icon} {label}",
            fg=fg, bg=bg, font=("Microsoft YaHei UI", 9),
            padx=7, pady=4, cursor="hand2",
        )
        b.pack(side="left", padx=2)
        b.bind("<Enter>", lambda e, bb=b, p=primary: bb.configure(bg="#3d7dc9" if p else "#4a5160"))
        b.bind("<Leave>", lambda e, bb=b, p=primary: bb.configure(bg=bg))
        b.bind("<Button-1>", lambda e, c=command: c())

    def _restool_hover_tb(self, name, btn):
        """工具按钮离开时恢复样式（选中态保持高亮）。"""
        if name == self.tool:
            btn.configure(bg="#4a90e2", fg="#ffffff")
        else:
            btn.configure(bg="#3a3f47", fg="#f5f6f8")

    def _tb_start_follow(self, event):
        """按住左键：记录鼠标相对工具栏左上角的偏移，开始自由移动。"""
        self._tb_follow = True
        self._tb_docked = False  # 拖离后不再吸附
        self._tb_follow_off_x = event.x
        self._tb_follow_off_y = event.y

    def _tb_do_follow(self, event):
        """自由移动：先跟随鼠标，再按相对编辑框的偏移吸附回去。

        拖动后工具栏保持"跟随编辑框"，但位置是拖动到的相对偏移
        （不再是吸附居中），因此移动/缩放编辑框时工具栏会跟着走。
        """
        if not self._tb_follow:
            return
        x = event.x_root - self._tb_follow_off_x
        y = event.y_root - self._tb_follow_off_y
        if not self.toolbar.winfo_exists():
            return
        self.toolbar.geometry(f"+{x}+{y}")
        # 记录工具栏相对对齐基准点的偏移，作为新的跟随基准
        anchor_x, anchor_y = self._anchor_point()
        self._tb_off_x = x - anchor_x
        self._tb_off_y = y - anchor_y

    def _tb_stop_follow(self, event):
        self._tb_follow = False

    def _tb_sync_with_window(self, event=None):
        """跟随编辑框：窗口移动/缩放/隐藏时同步工具栏位置。"""
        if not self.toolbar.winfo_exists():
            return
        # 窗口被隐藏（最小化/withdraw）时，工具栏也隐藏
        if self.root.winfo_viewable() == 0:
            try:
                self.toolbar.withdraw()
            except tk.TclError:
                pass
            self._tb_last_geom = None
            return
        try:
            self.toolbar.deiconify()
        except tk.TclError:
            pass
        # 仅在编辑框位置/尺寸确实变化时才重新定位。
        # 选择工具时编辑框没动（但 <Configure>/轮询仍会触发），此时不重排，
        # 避免菜单栏跳动；编辑框一旦移动，立即跟随。
        geom = self._window_frame_rect()
        if geom == self._tb_last_geom:
            return
        self._tb_last_geom = geom
        self._position_toolbar()

    def _tb_poll(self, every_ms=100):
        """轮询兜底：周期性检查编辑框位置，确保工具栏始终跟随。"""
        self.root.after(every_ms, self._tb_poll)
        self._tb_sync_with_window()

    def _measure_toolbar_width(self):
        """测量工具栏自然宽度，并把编辑框宽度撑到不小于工具栏。"""
        if not self.toolbar.winfo_exists():
            return
        self.toolbar.update_idletasks()
        w = self.toolbar.winfo_reqwidth()
        if w <= 1:
            w = self.toolbar.winfo_width()
        if w > 1:
            self._tb_min_w = w
        # 让窗口重新渲染，按最小宽度调整尺寸
        self._render()
        self._position_toolbar()

    def _window_frame_rect(self):
        """返回编辑框外框（含标题栏、边框）的屏幕坐标 (left, top, right, bottom)。

        取窗口外框而不是客户区，才能把工具栏放在标题栏之上，避免压住标题栏。
        非 Windows 或获取失败时退回 Tk 的客户区矩形。
        """
        try:
            if sys.platform.startswith("win"):
                import ctypes
                from ctypes import wintypes
                hwnd = self.root.wm_frame()
                hwnd = int(hwnd, 16) if hwnd else self.root.winfo_id()
                rect = wintypes.RECT()
                if ctypes.windll.user32.GetWindowRect(
                        wintypes.HWND(hwnd), ctypes.byref(rect)):
                    if rect.right > rect.left and rect.bottom > rect.top:
                        return rect.left, rect.top, rect.right, rect.bottom
        except Exception:
            pass
        x, y = self.root.winfo_rootx(), self.root.winfo_rooty()
        return x, y, x + self.root.winfo_width(), y + self.root.winfo_height()

    def _anchor_point(self):
        """工具栏对齐基准点 (x, y)。

        x 用客户区左边界（Windows 外框含 ~8px 不可见的缩放边框，用外框会
        看起来偏左，客户区左边界才与窗口可见边缘对齐）；
        y 用外框顶（含标题栏），避免菜单栏压住标题栏。
        """
        _, top, _, _ = self._window_frame_rect()
        return self.root.winfo_rootx(), top

    def _position_toolbar(self):
        """定位工具栏。

        吸附态（初始）：与编辑框可见左边缘左对齐，放在窗口上方（不压标题栏、
        不遮挡图像）；上方放不下时放到下方。拖动后：保持拖动时记录的相对偏移。
        """
        if not self.toolbar.winfo_exists():
            return
        if self._tb_docked:
            self.toolbar.update_idletasks()
            tb_h = self.toolbar.winfo_height()
            _, top, _, bottom = self._window_frame_rect()
            anchor_x, anchor_y = self._anchor_point()
            # 左对齐于编辑框可见左边缘
            x = anchor_x
            # 默认放在窗口外框上方（含标题栏之外）；上方放不下才放到下方
            y = top - tb_h - 4
            if y < 0:
                y = bottom + 4
            # 上下都放不下（编辑框几乎占满屏幕）时，贴屏幕顶端（无法避免会压边）
            screen_h = self.root.winfo_screenheight()
            if y + tb_h > screen_h:
                y = max(0, top - tb_h - 4)
        else:
            # 跟随编辑框：左上角 = 对齐基准点 + 拖动时记录的偏移
            anchor_x, anchor_y = self._anchor_point()
            x = anchor_x + self._tb_off_x
            y = anchor_y + self._tb_off_y
        self.toolbar.geometry(f"+{x}+{y}")
        # 记录本次定位时的编辑框几何，供跟随判断使用
        self._tb_last_geom = self._window_frame_rect()

    def _style_flat_buttons(self):
        """让 ttk.Spinbox 在 Windows 下用浅色边框。"""
        try:
            style = ttk.Style()
            style.theme_use("vista" if sys.platform.startswith("win") else "clam")
            style.configure("TSpinbox", font=("Microsoft YaHei UI", 9))
        except Exception:
            pass

    def _mk_action_btn(self, parent, text, command, primary=False):
        bg = self.ACCENT if primary else self.BTN_BG
        fg = "#ffffff" if primary else self.FG
        hover = "#3d7dc9" if primary else self.BTN_HOVER
        btn = tk.Button(
            parent, text=text, fg=fg, bg=bg,
            activeforeground="#ffffff" if primary else self.FG,
            activebackground=hover,
            relief="flat", bd=0, padx=10, pady=4, cursor="hand2",
            font=("Microsoft YaHei UI", 9),
            command=command,
        )
        btn.pack(side="left", padx=3)
        btn.bind("<Enter>", lambda e, b=btn: b.configure(bg=hover))
        btn.bind("<Leave>", lambda e, b=btn: b.configure(bg=bg))
        return btn

    def _restool_hover(self, btn):
        """工具按钮离开时恢复背景（选中态保持深色，未选中恢复浅色）。"""
        for name, b in self._tool_btns.items():
            if b is btn:
                self._apply_tool_style(name, btn)
                return

    def _select_tool(self, name):
        self.tool = name
        self.tool_var.set(name)
        self._refresh_tool_btns()

    def _refresh_tool_btns(self):
        for name, btn in self._tool_btns.items():
            self._apply_tool_style(name, btn)

    def _apply_tool_style(self, name, btn):
        if name == self.tool:
            btn.configure(bg="#4a90e2", fg="#ffffff")
        else:
            btn.configure(bg="#3a3f47", fg="#f5f6f8")

    def _sync_clipboard(self):
        """把当前编辑结果同步到剪贴板（静默，失败不影响编辑）。"""
        try:
            copy_to_clipboard(self.image)
        except Exception:
            pass

    def _on_tool_change(self):
        self.tool = self.tool_var.get()

    def _pick_color(self):
        c = colorchooser.askcolor(initialcolor=self.color)
        if c[1]:
            self.color = c[1]
            self.color_btn.configure(bg=self.color)

    def _pick_canvas_bg(self):
        """选择画布背景色，应用并持久化。"""
        c = colorchooser.askcolor(
            initialcolor=self.canvas_bg,
            title="选择编辑背景颜色",
        )
        if c[1]:
            self.canvas_bg = c[1]
            self.canvas.configure(bg=self.canvas_bg)
            self.canvas_bg_btn.configure(bg=self.canvas_bg)
            # 持久化
            s = _load_settings()
            s["canvas_bg"] = self.canvas_bg
            _save_settings(s)

    def _render(self):
        """重绘：图像居中绘制，四周留白（工具栏悬浮于窗口外/上方）。"""
        self._photo = ImageTk.PhotoImage(self.image)
        img_w, img_h = self.image.size
        pad2 = 2 * self.PAD
        # 窗口/画布尺寸：图像尺寸 + 四周留白；宽度不小于工具栏宽度（与菜单栏持平）
        target_w = max(img_w + pad2, getattr(self, "_tb_min_w", 0) or 0)
        target_h = img_h + pad2
        if (img_w, img_h) != (self.win_w, self.win_h) or \
                target_w != getattr(self, "_win_px_w", 0):
            self.win_w, self.win_h = img_w, img_h
            self._win_px_w = target_w
            self.root.geometry(f"{target_w}x{target_h}")
            self.canvas.configure(width=target_w, height=target_h)
        # 图像在画布内水平、垂直居中
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        if cw <= 1:
            cw = target_w
        if ch <= 1:
            ch = target_h
        self._off_x = max(0, (cw - img_w) // 2)
        self._off_y = max(0, (ch - img_h) // 2)
        self.canvas.delete("all")
        self.canvas.create_image(self._off_x, self._off_y, anchor="nw", image=self._photo)
        # 裁剪后窗口尺寸变化，仅吸附状态下才重新定位（自由移动状态不打断）
        if getattr(self, "_tb_docked", True):
            self._position_toolbar()

    def _push_undo(self):
        self.undo.push(self.image.copy())

    def _undo(self):
        prev = self.undo.pop()
        if prev:
            self.image = prev
            self._render()
            self._sync_clipboard()

    def _to_img(self, cx, cy):
        """画布坐标 → 图像坐标（减去居中偏移并限制在图像范围内）。"""
        iw, ih = self.image.size
        ox, oy = getattr(self, "_off_x", 0), getattr(self, "_off_y", 0)
        x = max(0, min(iw - 1, cx - ox))
        y = max(0, min(ih - 1, cy - oy))
        return x, y

    def _in_image(self, cx, cy):
        """画布坐标是否落在图像区域内（留白区域内不算）。"""
        iw, ih = self.image.size
        ox, oy = getattr(self, "_off_x", 0), getattr(self, "_off_y", 0)
        return ox <= cx < ox + iw and oy <= cy < oy + ih

    def _on_press(self, event):
        # 点击留白区域（图像之外）不开始绘制
        if not self._in_image(event.x, event.y):
            return
        if self.tool == "text":
            self._input_text(*self._to_img(event.x, event.y))
            return
        self.drawing = True
        self.draw_start = self._to_img(event.x, event.y)
        self._push_undo()
        if self.tool == "brush":
            self._brush_last = self.draw_start
            d = ImageDraw.Draw(self.image)
            d.point(self.draw_start, fill=self.color)
            self._render()
        else:
            self._preview_id = None

    @staticmethod
    def _alt_pressed(event=None):
        """事件发生时是否真正按住 Alt。

        Windows 下直接用 GetAsyncKeyState 读 VK_MENU，最可靠；不依赖
        event.state 的位（Tk 在不同平台/场景下位的含义不一致，容易误判成
        按住 Alt，导致不按 Alt 时也被约束成正圆）。
        """
        if sys.platform.startswith("win"):
            try:
                import ctypes
                VK_MENU = 0x12  # Alt
                return bool(ctypes.windll.user32.GetAsyncKeyState(VK_MENU) & 0x8000)
            except Exception:
                pass
        state = getattr(event, "state", 0) or 0
        return bool(state & 0x20000)

    def _ellipse_pos(self, pos, square=False):
        """圆形工具的目标点；square 为 True（按住 Alt）时约束成正圆。

        以起点为基准，取横纵位移的较大值作为边长，方向跟随鼠标，
        并限制在图像范围内以免越界。
        """
        x0, y0 = self.draw_start
        x1, y1 = pos
        if square:
            dx, dy = x1 - x0, y1 - y0
            d = max(abs(dx), abs(dy))
            sx = 1 if dx >= 0 else -1
            sy = 1 if dy >= 0 else -1
            # 边长不超过图像边界，保证约束后仍是正方形
            max_x = (self.image.width - 1 - x0) if sx > 0 else x0
            max_y = (self.image.height - 1 - y0) if sy > 0 else y0
            d = max(0, min(d, max_x, max_y))
            x1 = x0 + sx * d
            y1 = y0 + sy * d
        return x1, y1

    def _on_drag(self, event):
        if not self.drawing:
            return
        pos = self._to_img(event.x, event.y)
        if self.tool == "ellipse":
            pos = self._ellipse_pos(pos, self._alt_pressed(event))
        if self.tool == "brush":
            draw_brush(self.image, [self._brush_last, pos], self.color, self.width)
            self._brush_last = pos
            self._render()
        else:
            # 预览用画布坐标（= 图像坐标 + 偏移）
            ox, oy = getattr(self, "_off_x", 0), getattr(self, "_off_y", 0)
            self._draw_preview(self.draw_start[0] + ox, self.draw_start[1] + oy,
                               pos[0] + ox, pos[1] + oy)

    def _draw_preview(self, x0, y0, x1, y1):
        if self._preview_id:
            self.canvas.delete(self._preview_id)
        if self.tool == "rect":
            self._preview_id = self.canvas.create_rectangle(
                x0, y0, x1, y1, outline=self.color, width=self.width)
        elif self.tool == "ellipse":
            self._preview_id = self.canvas.create_oval(
                x0, y0, x1, y1, outline=self.color, width=self.width)
        elif self.tool == "crop":
            self._preview_id = self.canvas.create_rectangle(
                x0, y0, x1, y1, outline="white", width=2, dash=(6, 3))
        elif self.tool == "arrow":
            self._preview_id = self.canvas.create_line(
                x0, y0, x1, y1, fill=self.color, width=self.width,
                arrow="last", arrowshape=(10, 15, 5))

    def _on_release(self, event):
        if not self.drawing:
            return
        self.drawing = False
        pos = self._to_img(event.x, event.y)
        if self.tool == "rect":
            self._canvas_delete_preview()
            # 规范化坐标（PIL 要求 x1>=x0, y1>=y0，否则拖拽方向反向会报错）
            x0, y0 = self.draw_start
            x1, y1 = pos
            draw_rect(self.image,
                      [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)],
                      self.color, self.width)
            self._render()
            self._sync_clipboard()
        elif self.tool == "ellipse":
            self._canvas_delete_preview()
            # 按住 Alt 时约束为正圆（与拖拽预览保持一致）
            x1, y1 = self._ellipse_pos(pos, self._alt_pressed(event))
            x0, y0 = self.draw_start
            # 规范化坐标后画椭圆（PIL 要求 x1>=x0, y1>=y0）
            draw_ellipse(self.image,
                         [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)],
                         self.color, self.width)
            self._render()
            self._sync_clipboard()
        elif self.tool == "arrow":
            self._canvas_delete_preview()
            self._draw_arrow(self.draw_start, pos)
            self._render()
            self._sync_clipboard()
        elif self.tool == "mosaic":
            self._canvas_delete_preview()
            l, t = self.draw_start
            r, b = pos
            l, r = min(l, r), max(l, r)
            t, b = min(t, b), max(t, b)
            # 限制在图像范围内，并跳过无面积的选区（单击或水平/垂直拖拽），
            # 否则 crop/resize 会因为宽或高为 0 报错
            r = min(r, self.image.width)
            b = min(b, self.image.height)
            if r > l and b > t:
                self.image = mosaic(self.image, l, t, r, b, block=8)
                self._render()
                self._sync_clipboard()
        elif self.tool == "crop":
            self._canvas_delete_preview()
            self._do_crop(self.draw_start, pos)
        elif self.tool == "brush":
            self._render()
            self._sync_clipboard()

    def _do_crop(self, start, end):
        """把图像裁剪到 start~end 围成的区域（可撤销）。

        单击（宽高为 0）视为无效，忽略；越界坐标限制在图像范围内。
        """
        x0, y0 = start
        x1, y1 = end
        l, r = min(x0, x1), max(x0, x1)
        t, b = min(y0, y1), max(y0, y1)
        if r == l or b == t:
            return  # 单击（无面积），忽略
        r = min(r, self.image.width)
        b = min(b, self.image.height)
        if b <= t or r <= l:
            return  # 越界裁剪后无有效区域
        self.image = self.image.crop((l, t, r, b))
        self._render()
        self._sync_clipboard()

    def _canvas_delete_preview(self):
        if self._preview_id:
            self.canvas.delete(self._preview_id)
            self._preview_id = None

    def _draw_arrow(self, start, end):
        draw_arrow(self.image, start, end, self.color, self.width)

    def _input_text(self, x, y):
        top = tk.Toplevel(self.root)
        top.title("输入文字")
        top.geometry("300x80")
        top.transient(self.root)
        top.grab_set()
        entry = tk.Entry(top, font=("Arial", 12))
        entry.pack(padx=8, pady=8, fill="x", expand=True)
        entry.focus_set()

        def commit(event=None):
            text = entry.get()
            if text:
                self._push_undo()
                draw_text(self.image, (x, y), text, self.color, 12)
                self._render()
                self._sync_clipboard()
            top.destroy()

        entry.bind("<Return>", commit)
        tk.Button(top, text="确定", command=commit).pack(pady=4)
        entry.bind("<Escape>", lambda e: top.destroy())
        top.bind("<Escape>", lambda e: top.destroy())

    def _save(self):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = filedialog.asksaveasfilename(
            defaultextension=".png",
            filetypes=[("PNG", "*.png"), ("JPEG", "*.jpg")],
            initialfile=f"截图_{ts}.png")
        if path:
            try:
                if path.lower().endswith((".jpg", ".jpeg")):
                    self.image.convert("RGB").save(path)
                else:
                    self.image.save(path)
            except Exception as e:
                from tkinter import messagebox
                messagebox.showerror("保存失败", str(e))

    def _copy(self):
        try:
            copy_to_clipboard(self.image)
        except Exception as e:
            from tkinter import messagebox
            messagebox.showerror("复制失败", str(e))

    def _ocr(self):
        """识别图中文字，弹窗显示，可复制到剪贴板。"""
        from tkinter import messagebox
        import numpy as np
        import threading

        engine = _get_ocr_engine()
        if engine is None:
            messagebox.showerror(
                "识别失败",
                "未安装 OCR 引擎（rapidocr-onnxruntime）。\n请执行：pip install rapidocr-onnxruntime")
            return

        def run():
            try:
                arr = np.array(self.image.convert("RGB"))
                result = engine(arr)
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("识别失败", str(e)))
                return
            text = ocr_result_to_text(result)
            self.root.after(0, lambda t=text: self._show_ocr_result(t))

        # OCR 较慢，放到工作线程，避免卡住界面
        threading.Thread(target=run, daemon=True).start()

    def _show_ocr_result(self, text):
        """弹窗显示识别结果，提供"复制全部"。"""
        if not text:
            from tkinter import messagebox
            messagebox.showinfo("识别文字", "未识别到文字。")
            return

        dlg = tk.Toplevel(self.root)
        dlg.title("识别文字")
        dlg.geometry("520x360")
        dlg.transient(self.root)

        txt = tk.Text(dlg, wrap="word", font=("Consolas", 11), relief="solid", bd=1)
        txt.pack(fill="both", expand=True, padx=8, pady=(8, 4))
        txt.insert("1.0", text)
        txt.config(state="disabled")

        def copy_all():
            try:
                import win32clipboard
                win32clipboard.OpenClipboard()
                try:
                    win32clipboard.EmptyClipboard()
                    win32clipboard.SetClipboardData(13, text)  # CF_UNICODETEXT
                finally:
                    win32clipboard.CloseClipboard()
            except Exception:
                pass
            dlg.destroy()

        bar = tk.Frame(dlg)
        bar.pack(fill="x", padx=8, pady=(0, 8))
        tk.Button(bar, text="复制全部", command=copy_all,
                  font=("Microsoft YaHei UI", 9)).pack(side="right")
        tk.Button(bar, text="关闭", command=dlg.destroy,
                  font=("Microsoft YaHei UI", 9)).pack(side="right", padx=(0, 6))
        dlg.grab_set()

    def _on_root_delete(self):
        """编辑框根窗口被系统关闭（如点任务栏关闭按钮）时，一并销毁工具栏。"""
        if getattr(self, "toolbar", None) is not None and self.toolbar.winfo_exists():
            try:
                self.toolbar.destroy()
            except tk.TclError:
                pass
        self.root.destroy()
        if self.on_close:
            self.on_close()

    def _close(self):
        # 先销毁悬浮工具栏，再销毁主窗口
        if getattr(self, "toolbar", None) is not None and self.toolbar.winfo_exists():
            try:
                self.toolbar.destroy()
            except tk.TclError:
                pass
        self.root.destroy()
        if self.on_close:
            self.on_close()

    def show(self):
        """运行编辑器。

        共享根窗口（root 传入）：非阻塞，直接返回，窗口由共享 mainloop 驱动。
        自建根窗口（root 未传）：阻塞运行 mainloop，直到关闭。
        """
        if self._owns_root:
            self.root.mainloop()
