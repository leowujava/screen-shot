import tkinter as tk


class RegionSelector:
    """全屏透明覆盖层，拖拽框选区域。

    x, y: 窗口左上角在虚拟桌面中的坐标（使窗口 (0,0) 对齐虚拟桌面 (0,0)）
    width, height: 虚拟桌面尺寸
    on_select: 选区结束回调，参数为 (left, top, width, height) 或 None（取消）
    root: 可选的共享 Tk 根窗口。传入时选区作为其 Toplevel 打开（非阻塞）；
          不传则自建根窗口并阻塞运行（show）。
    """

    MIN_SIZE = 5

    def __init__(self, x, y, width, height, on_select, root=None):
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.on_select = on_select
        self.result = None
        self.start = None
        self._rect_id = None
        self._label_id = None
        self._owns_root = root is None

        if self._owns_root:
            self.root = tk.Tk()
            self.root.title("")
        else:
            # 共享根窗口：选区作为其上的无边框置顶覆盖层
            self.root = tk.Toplevel(root)
            self.root.title("")
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.attributes("-fullscreen", True)
        self.root.attributes("-alpha", 0.2)
        self.root.configure(bg="black")
        # 置顶 + 无边框
        self.root.wm_attributes("-topmost", True)
        self.root.wm_overrideredirect(True)

        self.canvas = tk.Canvas(self.root, bg="black", highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.root.bind("<Escape>", lambda e: self._cancel())
        self.root.bind("<Button-3>", lambda e: self._cancel())

        self.root.focus_force()
        self._finished = False

    def _finish(self, result):
        """结束选区：记录结果并关闭选区窗口，再调 on_select 回调。

        用 after(0) 推迟销毁+回调，避免在事件处理里同步销毁窗口导致事件循环
        异常（遮罩卡住 / Tcl 解释器被破坏）。共享根窗口模式下不退出 mainloop。
        """
        if self._finished:
            return
        self._finished = True
        self.result = result
        self._clear()
        self.root.after(0, self._exit)

    def _exit(self):
        if self._owns_root:
            try:
                self.root.destroy()
            except tk.TclError:
                pass
        else:
            try:
                self.root.withdraw()
            except tk.TclError:
                pass
        self.on_select(self.result)

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

    def _on_press(self, event):
        self.start = (event.x, event.y)
        self._rect_id = self.canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline="red", width=2)
        self._label_id = self.canvas.create_text(
            event.x, event.y - 10, text="", fill="white", font=("Arial", 10))

    def _on_drag(self, event):
        if not self.start:
            return
        self.canvas.coords(self._rect_id, self.start[0], self.start[1], event.x, event.y)
        r = self._calc_region(self.start[0], self.start[1], event.x, event.y)
        if r:
            self.canvas.itemconfigure(self._label_id, text=f"{r[2]} x {r[3]}")
        else:
            self.canvas.itemconfigure(self._label_id, text="")

    def _on_release(self, event):
        if not self.start:
            return
        r = self._calc_region(self.start[0], self.start[1], event.x, event.y)
        self.start = None
        self._finish(r)

    def _clear(self):
        for item in (self._rect_id, self._label_id):
            if item:
                self.canvas.delete(item)
        self._rect_id = None
        self._label_id = None

    def _cancel(self):
        self._finish(None)

    def show(self):
        """运行选区窗口。

        共享根窗口（root 传入）：非阻塞，直接返回，on_select 在后续事件中触发。
        自建根窗口（root 未传）：阻塞运行 mainloop，直到选区结束。
        """
        if self._owns_root:
            self.root.mainloop()
