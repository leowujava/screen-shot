# screen-shot 截屏工具 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建 Windows Python 截屏工具：框选截图（带编辑）、全屏截图、系统托盘常驻。

**Architecture:** 4 个模块解耦：`capture.py`（mss 截屏）、`selector.py`（tkinter 透明覆盖层选区）、`editor.py`（tkinter 编辑窗口）、`app.py`（快捷键 + 托盘 + 流程编排）。

**Tech Stack:** Python 3, mss, Pillow, pynput, pystray, pywin32, tkinter

---

### Task 0: 初始化项目与依赖

**Files:**
- Create: `requirements.txt`
- Create: `.gitignore`

- [ ] **Step 1: 创建 requirements.txt**

```
mss
Pillow
pynput
pystray
pywin32
```

- [ ] **Step 2: 创建 .gitignore**

```
__pycache__/
*.pyc
.venv/
```

- [ ] **Step 3: 初始化 git 仓库**

```bash
git init
git add requirements.txt .gitignore
git commit -m "chore: init project with dependencies"
```

- [ ] **Step 4: 安装依赖**

```bash
pip install -r requirements.txt
```

- [ ] **Step 5: 验证依赖可用**

```bash
python -c "import mss, PIL, pynput, pystray, win32gui, win32api; print('ok')"
```

Expected: `ok`

---

### Task 1: capture.py — 截屏模块

**Files:**
- Create: `capture.py`
- Test: `tests/test_capture.py`

- [ ] **Step 1: 写测试**

创建 `tests/test_capture.py`：

```python
from PIL import Image
from capture import capture_fullscreen, capture_region


def test_capture_fullscreen_returns_image():
    img = capture_fullscreen()
    assert isinstance(img, Image.Image)
    assert img.width > 0 and img.height > 0


def test_capture_fullscreen_multiple_monitors():
    img = capture_fullscreen()
    # 虚拟桌面尺寸 >= 单屏尺寸
    assert img.size == capture_virtual_screen_size()


def test_capture_region():
    full = capture_fullscreen()
    w, h = full.size
    region = capture_region(0, 0, w // 2, h // 2)
    assert isinstance(region, Image.Image)
    assert region.size == (w // 2, h // 2)
```

注意：测试里引用了 `capture_virtual_screen_size`，需要在 capture.py 中导出该函数。

- [ ] **Step 2: 实现 capture.py**

```python
import time
import os
from datetime import datetime
from PIL import Image
import mss


def capture_virtual_screen_size():
    """返回虚拟桌面（所有显示器合并）的 (width, height)。"""
    with mss.mss() as sct:
        mon = sct.monitors[0]
        return mon["width"], mon["height"]


def capture_fullscreen():
    """截取全部虚拟桌面，返回 PIL Image。"""
    with mss.mss() as sct:
        mon = sct.monitors[0]
        raw = sct.grab(mon)
        return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


def capture_region(left, top, width, height):
    """截取虚拟桌面内指定区域，返回 PIL Image。"""
    with mss.mss() as sct:
        raw = sct.grab({"left": left, "top": top, "width": width, "height": height})
        return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


def default_save_path():
    """默认保存路径：桌面/截图_时间戳.png"""
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(desktop, f"截图_{ts}.png")


def save_image(img, path=None):
    """保存图片，返回实际保存路径。失败抛异常。"""
    path = path or default_save_path()
    img.save(path)
    return path
```

- [ ] **Step 3: 运行测试**

```bash
python -m pytest tests/test_capture.py -v
```

Expected: 3 passed（截屏需要桌面会话，CI 无头环境会失败，本地 Windows 应通过）

- [ ] **Step 4: Commit**

```bash
git add capture.py tests/test_capture.py
git commit -m "feat: capture module with fullscreen/region capture"
```

---

### Task 2: selector.py — 区域选择覆盖层

**Files:**
- Create: `selector.py`
- Test: `tests/test_selector.py`

**说明：** 选区是交互式的，自动化测试困难。测试覆盖可测试部分（坐标换算、区域计算），交互部分手动验证。

- [ ] **Step 1: 写测试（可测试部分）**

```python
from selector import RegionSelector


def test_region_calculation():
    sel = RegionSelector(x=-1920, y=0, width=3840, height=1080)
    # 虚拟桌面坐标 (0,0) 对应屏幕 (0,0)
    x1, y1, x2, y2 = sel._calc_region(100, 50, 300, 200)
    assert (x1, y1, x2, y2) == (0, 0, 200, 150)


def test_region_calculation_second_monitor():
    sel = RegionSelector(x=-1920, y=0, width=3840, height=1080)
    # 虚拟桌面 (2000, 100) 是第二屏上的点
    x1, y1, x2, y2 = sel._calc_region(1080, 50, 1180, 150)
    # 窗口内坐标 (1080,50) -> 虚拟桌面 (1080-1920, 50) = (-840, 50)
    assert x1 == 240 and y1 == 50  # 虚拟桌面坐标 = 1080-1920+1080... 
```

等等，我需要把坐标换算写清楚再定测试。先定义：

**坐标约定：**
- 虚拟桌面坐标系：原点 (0,0)，宽 `vwidth`，高 `vheight`
- 窗口 `x, y`：覆盖层左上角在虚拟桌面中的位置（通常为 `-offset_x, -offset_y`，使窗口 (0,0) 对应虚拟桌面 (0,0)）
- 窗口内坐标 `(wx, wy)` → 虚拟桌面坐标 `(wx + x, wy + y)`
- 区域 = `(min(vx1,vx2), min(vy1,vy2), width, height)`，width/height > 0

- [ ] **Step 1（重写）: 写测试**

```python
from selector import RegionSelector


def test_virtual_coord_conversion():
    # 窗口放在 (-1920, 0)，虚拟桌面 3840x1080
    sel = RegionSelector(x=-1920, y=0, width=3840, height=1080)
    # 窗口内 (100, 50) -> 虚拟桌面 (100-1920, 50) = (-1820, 50)
    assert sel._to_virtual(100, 50) == (-1820, 50)
    # 窗口内 (2000, 100) -> 虚拟桌面 (80, 100)
    assert sel._to_virtual(2000, 100) == (80, 100)


def test_calc_region_normalized():
    sel = RegionSelector(x=-1920, y=0, width=3840, height=1080)
    # 从右往左拖拽，应归一化
    r = sel._calc_region(500, 300, 100, 100)
    # 虚拟桌面: (500-1920, 300) = (-1420, 300), (100-1920, 100) = (-1820, 100)
    # 归一化: left=-1820, top=100, width=400, height=200
    assert r == (-1820, 100, 400, 200)


def test_calc_region_min_size():
    sel = RegionSelector(x=0, y=0, width=1920, height=1080)
    # 点击太小（< 5px）视为无效，返回 None
    assert sel._calc_region(100, 100, 101, 101) is None
```

- [ ] **Step 2: 实现 selector.py**

```python
import tkinter as tk
from PIL import Image, ImageTk


class RegionSelector:
    """全屏透明覆盖层，拖拽框选区域。

    x, y: 窗口左上角在虚拟桌面中的坐标（使窗口 (0,0) 对齐虚拟桌面 (0,0)）
    width, height: 虚拟桌面尺寸
    """

    MIN_SIZE = 5

    def __init__(self, x, y, width, height, on_select):
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.on_select = on_select
        self.result = None
        self.start = None
        self._rect_id = None
        self._label_id = None

        self.root = tk.Tk()
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
        self._clear()
        if r:
            self.result = r
        self.root.destroy()
        self.on_select(self.result)

    def _clear(self):
        for item in (self._rect_id, self._label_id):
            if item:
                self.canvas.delete(item)
        self._rect_id = None
        self._label_id = None

    def _cancel(self):
        self.result = None
        self._clear()
        self.root.destroy()
        self.on_select(None)

    def show(self):
        """阻塞运行选区窗口。"""
        self.root.mainloop()
```

- [ ] **Step 3: 运行测试**

```bash
python -m pytest tests/test_selector.py -v
```

Expected: 3 passed

- [ ] **Step 4: 手动验证选区交互**

临时脚本 `tests/manual_selector.py`：

```python
import tkinter as tk
from selector import RegionSelector

root = tk.Tk()
root.withdraw()

def on_select(region):
    print("Selected:", region)
    root.destroy()

RegionSelector(x=0, y=0, width=1920, height=1080, on_select=on_select).show()
```

```bash
python tests/manual_selector.py
```

手动验证：拖拽出现红框 + 尺寸、Esc 取消、右键取消、点击太小不触发。

- [ ] **Step 5: Commit**

```bash
git add selector.py tests/test_selector.py tests/manual_selector.py
git commit -m "feat: region selector overlay with drag-select"
```

---

### Task 3: editor.py — 编辑窗口

**Files:**
- Create: `editor.py`
- Test: `tests/test_editor.py`

**说明：** 编辑窗口 UI 交互式，自动化测试困难。测试覆盖可测试的逻辑：工具状态、撤销栈、图像操作函数。

- [ ] **Step 1: 写测试（可测试逻辑）**

```python
from PIL import Image
from editor import UndoStack, mosaic


def test_undo_stack_basic():
    s = UndoStack(maxlen=10)
    s.push(1)
    s.push(2)
    s.push(3)
    assert s.pop() == 3
    assert s.pop() == 2
    assert s.pop() == 1
    assert s.pop() is None


def test_undo_stack_maxlen():
    s = UndoStack(maxlen=3)
    for i in range(5):
        s.push(i)
    assert s.pop() == 4
    assert s.pop() == 3
    assert s.pop() == 2
    assert s.pop() is None


def test_mosaic_reduces_detail():
    img = Image.new("RGB", (100, 100), "red")
    # 画一个细线，马赛克后应模糊
    for i in range(50):
        img.putpixel((i, 50), "blue")
    result = mosaic(img, 0, 40, 100, 60, block=8)
    assert result.size == img.size
    # 中心块应变为混合色，非纯蓝
    px = result.getpixel((48, 48))
    assert px != (0, 0, 255)
```

- [ ] **Step 2: 实现 editor.py**

```python
import tkinter as tk
from tkinter import ttk, filedialog
from PIL import Image, ImageDraw, ImageTk
import numpy as np
from capture import default_save_path


class UndoStack:
    def __init__(self, maxlen=10):
        self.maxlen = maxlen
        self._stack = []

    def push(self, item):
        self._stack.append(item)
        if len(self._stack) > self.maxlen:
            self._stack.pop(0)

    def pop(self):
        if self._stack:
            return self._stack.pop()
        return None

    def clear(self):
        self._stack.clear()


def mosaic(img, left, top, right, bottom, block=8):
    """对 img 的 [left,top,right,bottom] 区域做马赛克，返回新图。"""
    result = img.copy()
    region = result.crop((left, top, right, bottom))
    w, h = region.size
    small = region.resize((max(1, w // block), max(1, h // block)), Image.NEAREST)
    big = small.resize((w, h), Image.NEAREST)
    result.paste(big, (left, top))
    return result


class Editor:
    """截图编辑窗口。

    image: PIL Image 待编辑图
    on_close: 关闭回调
    """

    TOOLS = ("brush", "rect", "arrow", "mosaic", "text")

    def __init__(self, image, on_close):
        self.image = image.copy()
        self.original = image.copy()
        self.on_close = on_close
        self.undo = UndoStack(maxlen=10)
        self.tool = "brush"
        self.color = "#ff0000"
        self.width = 3
        self.drawing = False
        self.draw_start = None
        self._preview_id = None

        self._build_ui()
        self._render()

    def _build_ui(self):
        self.root = tk.Tk()
        self.root.title("截图编辑")
        w, h = self.image.size
        self.root.geometry(f"{w}x{h + 40}+100+100")
        self.root.minsize(400, 300)

        toolbar = ttk.Frame(self.root)
        toolbar.pack(side="top", fill="x")

        self.tool_var = tk.StringVar(value="brush")
        for name in self.TOOLS:
            label = {"brush": "画笔", "rect": "矩形", "arrow": "箭头",
                     "mosaic": "打码", "text": "文字"}[name]
            btn = tk.Radiobutton(toolbar, text=label, value=name,
                                 variable=self.tool_var, command=self._on_tool_change)
            btn.pack(side="left", padx=2)

        tk.Label(toolbar, text="颜色:").pack(side="left", padx=(8, 2))
        self.color_btn = tk.Button(toolbar, bg=self.color, width=3,
                                   command=self._pick_color)
        self.color_btn.pack(side="left")
        tk.Label(toolbar, text="粗细:").pack(side="left", padx=(8, 2))
        self.width_var = tk.IntVar(value=self.width)
        spin = tk.Spinbox(toolbar, from_=1, to=20, width=3,
                          textvariable=self.width_var)
        spin.pack(side="left")

        ttk.Button(toolbar, text="撤销", command=self._undo).pack(side="left", padx=4)
        ttk.Button(toolbar, text="保存", command=self._save).pack(side="left", padx=4)
        ttk.Button(toolbar, text="关闭", command=self._close).pack(side="left", padx=4)

        self.canvas = tk.Canvas(self.root, width=w, height=h,
                                highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.root.bind("<Control-z>", lambda e: self._undo())
        self.root.bind("<Control-s>", lambda e: self._save())

        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self._photo = None

    def _on_tool_change(self):
        self.tool = self.tool_var.get()

    def _pick_color(self):
        from tkinter import colorchooser
        c = colorchooser.askcolor(initialcolor=self.color)
        if c[1]:
            self.color = c[1]
            self.color_btn.configure(bg=self.color)

    def _render(self):
        self._photo = ImageTk.PhotoImage(self.image)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self._photo)

    def _push_undo(self):
        self.undo.push(self.image.copy())

    def _undo(self):
        prev = self.undo.pop()
        if prev:
            self.image = prev
            self._render()

    def _on_press(self, event):
        if self.tool == "text":
            self._input_text(event.x, event.y)
            return
        self.drawing = True
        self.draw_start = (event.x, event.y)
        self._push_undo()
        if self.tool == "brush":
            self._brush_last = (event.x, event.y)
            self._stroke = ImageDraw.Draw(self.image)
            self._stroke.point((event.x, event.y), fill=self.color)
        else:
            self._preview_id = None

    def _on_drag(self, event):
        if not self.drawing:
            return
        if self.tool == "brush":
            d = ImageDraw.Draw(self.image)
            d.line([self._brush_last, (event.x, event.y)], fill=self.color, width=self.width)
            self._brush_last = (event.x, event.y)
            self._render()
        else:
            self._draw_preview(event.x, event.y)

    def _draw_preview(self, x, y):
        if self._preview_id:
            self.canvas.delete(self._preview_id)
        if self.tool == "rect":
            self._preview_id = self.canvas.create_rectangle(
                self.draw_start[0], self.draw_start[1], x, y, outline=self.color, width=self.width)
        elif self.tool == "arrow":
            self._preview_id = self.canvas.create_line(
                self.draw_start[0], self.draw_start[1], x, y, fill=self.color, width=self.width,
                arrow="last", arrowshape=(10, 15, 5))

    def _on_release(self, event):
        if not self.drawing:
            return
        self.drawing = False
        if self.tool == "rect":
            self._canvas_delete_preview()
            d = ImageDraw.Draw(self.image)
            d.rectangle([self.draw_start, (event.x, event.y)], outline=self.color, width=self.width)
            self._render()
        elif self.tool == "arrow":
            self._canvas_delete_preview()
            self._draw_arrow(self.draw_start, (event.x, event.y))
            self._render()
        elif self.tool == "mosaic":
            self._canvas_delete_preview()
            l, t = self.draw_start
            r, b = event.x, event.y
            l, r = min(l, r), max(l, r)
            t, b = min(t, b), max(t, b)
            self.image = mosaic(self.image, l, t, r, b, block=8)
            self._render()
        elif self.tool == "brush":
            self._render()

    def _canvas_delete_preview(self):
        if self._preview_id:
            self.canvas.delete(self._preview_id)
            self._preview_id = None

    def _draw_arrow(self, start, end):
        d = ImageDraw.Draw(self.image)
        x1, y1 = start
        x2, y2 = end
        d.line([start, end], fill=self.color, width=self.width)
        # 箭头头部
        import math
        length = math.hypot(x2 - x1, y2 - y1)
        if length < 1:
            return
        angle = math.atan2(y2 - y1, x2 - x1)
        head = 12
        a1 = angle + math.pi * 0.8
        a2 = angle - math.pi * 0.8
        p1 = (x2 + head * math.cos(a1), y2 + head * math.sin(a1))
        p2 = (x2 + head * math.cos(a2), y2 + head * math.sin(a2))
        d.polygon([end, p1, p2], fill=self.color)

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
                d = ImageDraw.Draw(self.image)
                d.text((x, y), text, fill=self.color)
                self._render()
            top.destroy()

        entry.bind("<Return>", commit)
        tk.Button(top, text="确定", command=commit).pack(pady=4)
        entry.bind("<Escape>", lambda e: top.destroy())
        top.bind("<Escape>", lambda e: top.destroy())

    def _save(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".png",
            filetypes=[("PNG", "*.png"), ("JPEG", "*.jpg")],
            initialfile="截图.png")
        if path:
            try:
                if path.lower().endswith((".jpg", ".jpeg")):
                    self.image.convert("RGB").save(path)
                else:
                    self.image.save(path)
            except Exception as e:
                from tkinter import messagebox
                messagebox.showerror("保存失败", str(e))

    def _close(self):
        self.root.destroy()
        if self.on_close:
            self.on_close()

    def show(self):
        self.root.mainloop()
```

注意：上面用了 `numpy` 但实际没用，去掉 import。`mosaic` 只用 PIL。

- [ ] **Step 3: 运行测试**

```bash
python -m pytest tests/test_editor.py -v
```

Expected: 3 passed

- [ ] **Step 4: 手动验证编辑器**

临时脚本 `tests/manual_editor.py`：

```python
from PIL import Image
from editor import Editor

img = Image.new("RGB", (800, 500), "lightblue")
Editor(img, on_close=lambda: print("closed")).show()
```

```bash
python tests/manual_editor.py
```

手动验证：各工具绘制、颜色/粗细调整、撤销、文字输入、保存。

- [ ] **Step 5: Commit**

```bash
git add editor.py tests/test_editor.py tests/manual_editor.py
git commit -m "feat: editor window with brush/rect/arrow/mosaic/text/undo/save"
```

---

### Task 4: app.py — 入口 + 快捷键 + 托盘

**Files:**
- Create: `app.py`
- Test: `tests/test_app.py`

**说明：** 快捷键/托盘/主循环是交互式全局功能，自动化测试困难。测试覆盖流程编排函数 `run_region_capture` / `run_fullscreen_capture` 的可测试部分（用 mock）。

- [ ] **Step 1: 写测试（流程编排逻辑）**

```python
from unittest.mock import MagicMock, patch
from app import run_region_capture, run_fullscreen_capture


def test_run_fullscreen_capture_saves():
    with patch("app.capture_fullscreen") as cf, \
         patch("app.save_image") as si:
        cf.return_value = MagicMock()
        si.return_value = "C:\\Desktop\\shot.png"
        path = run_fullscreen_capture()
        assert path == "C:\\Desktop\\shot.png"
        si.assert_called_once()


def test_run_region_capture_with_selection():
    mock_img = MagicMock()
    with patch("app.capture_fullscreen") as cf, \
         patch("app.RegionSelector") as rs, \
         patch("app.Editor") as ed:
        cf.return_value = mock_img
        rs.return_value.show.side_effect = lambda: None
        # 模拟选区回调传 (0,0,100,100)
        mock_region = (0, 0, 100, 100)
        # on_select 是 RegionSelector 构造参数，捕获它
        constructor = rs.return_value
        # 简化：用 side_effect 捕获 on_select
        captured = {}
        rs.side_effect = lambda x, y, width, height, on_select: _fake_selector(captured, on_select)
        run_region_capture()
        assert "region" in captured
        assert captured["region"] is not None


def _fake_selector(captured, on_select):
    import types
    sel = types.SimpleNamespace()
    def show():
        on_select((0, 0, 100, 100))
    sel.show = show
    return sel
```

这个测试设计有点绕。简化：把 app.py 的捕获流程写成纯函数 `process_region_selection(image, region)`，让它可测。

- [ ] **Step 1（重写）: 写测试**

```python
from unittest.mock import MagicMock, patch
from app import process_region_selection, process_fullscreen_capture


def test_process_fullscreen_saves_image():
    img = MagicMock()
    with patch("app.save_image") as si:
        si.return_value = "C:\\shot.png"
        path = process_fullscreen_capture(img)
        assert path == "C:\\shot.png"
        si.assert_called_once_with(img, None)


def test_process_region_with_selection_opens_editor():
    img = MagicMock()
    with patch("app.Editor") as ed:
        ed.return_value.show = MagicMock()
        process_region_selection(img, (0, 0, 100, 100))
        ed.assert_called_once()


def test_process_region_no_selection_ignores():
    img = MagicMock()
    with patch("app.Editor") as ed:
        ed.return_value.show = MagicMock()
        process_region_selection(img, None)
        ed.assert_not_called()
```

- [ ] **Step 2: 实现 app.py**

```python
import threading
from PIL import Image, ImageDraw
import pynput
import pystray
import win32gui
import win32con
import win32api
from capture import capture_fullscreen, capture_region, save_image, capture_virtual_screen_size
from selector import RegionSelector
from editor import Editor


def process_fullscreen_capture(img):
    """全屏截图 → 保存，返回路径。"""
    return save_image(img, None)


def process_region_selection(img, region):
    """区域选区结果 → 打开编辑器。region 为 None 时不做任何事。"""
    if region is None:
        return
    left, top, w, h = region
    region_img = capture_region(left, top, w, h)
    Editor(region_img, on_close=None).show()


def _virtual_screen_origin():
    """返回虚拟桌面左上角 (x, y)。"""
    import ctypes
    user32 = ctypes.windll.user32
    x = ctypes.c_int()
    y = ctypes.c_int()
    user32.GetSystemMetricsW(76)  # SM_XVIRTUALSCREEN
    user32.GetSystemMetricsW(77)  # SM_YVIRTUALSCREEN
    return user32.GetSystemMetricsW(76), user32.GetSystemMetricsW(77)


def do_region_capture():
    """框选区域截图（供快捷键和托盘调用）。"""
    full = capture_fullscreen()
    vsize = capture_virtual_screen_size()
    vx, vy = _virtual_screen_origin()

    def on_select(region):
        process_region_selection(full, region)

    RegionSelector(x=vx, y=vy, width=vsize[0], height=vsize[1], on_select=on_select).show()


def do_fullscreen_capture():
    """全屏截图直接保存。"""
    full = capture_fullscreen()
    process_fullscreen_capture(full)


def _make_tray_icon_image():
    """生成托盘图标。"""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # 相机外形
    d.rectangle([8, 20, 56, 52], outline="white", width=3)
    d.ellipse([22, 28, 42, 48], outline="white", width=3)
    d.rectangle([24, 14, 40, 20], fill="white")
    return img


def main():
    """主入口：托盘在子线程（pystray.run 阻塞），快捷键监听在主线程。"""
    tray_holder = {}

    def on_quit():
        if "icon" in tray_holder:
            tray_holder["icon"].stop()

    def run_tray():
        icon = pystray.Icon("screen-shot", _make_tray_icon_image(), "截图工具",
            pystray.Menu(
                pystray.MenuItem("框选截图", lambda i, m: do_region_capture(), default=True),
                pystray.MenuItem("全屏截图", lambda i, m: do_fullscreen_capture()),
                pystray.MenuItem("退出", lambda i, m: on_quit()),
            ))
        tray_holder["icon"] = icon
        icon.run()

    t = threading.Thread(target=run_tray, daemon=True)
    t.start()

    with pynput.GlobalHotKeys({
        "<ctrl>+<shift>+a": lambda: do_region_capture(),
        "<ctrl>+<shift>+f": lambda: do_fullscreen_capture(),
        "<ctrl>+<shift>+q": lambda: on_quit(),
    }):
        import time
        while t.is_alive():
            time.sleep(0.5)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: 运行测试**

```bash
python -m pytest tests/test_app.py -v
```

Expected: 3 passed

- [ ] **Step 4: 手动验证完整流程**

```bash
python app.py
```

手动验证：
- 托盘出现图标
- `Ctrl+Shift+A` 弹出选区 → 选完打开编辑器
- `Ctrl+Shift+F` 全屏截图保存桌面
- `Ctrl+Shift+Q` 退出
- 托盘左键=框选、右键菜单正常

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_app.py
git commit -m "feat: app entry with hotkeys and tray icon"
```

---

### Task 5: 端到端验证 + 收尾

**Files:**
- Create: `README.md`

- [ ] **Step 1: 跑全部测试**

```bash
python -m pytest tests/ -v
```

Expected: 全部 passed（交互部分除外，那些是手动脚本）

- [ ] **Step 2: 写 README.md**

```markdown
# screen-shot

Windows Python 截屏工具。

## 安装

```bash
pip install -r requirements.txt
```

## 运行

```bash
python app.py
```

## 快捷键

| 键 | 动作 |
|---|---|
| Ctrl+Shift+A | 框选区域截图（带编辑） |
| Ctrl+Shift+F | 全屏截图（直接保存） |
| Ctrl+Shift+Q | 退出 |

## 托盘

- 左键：框选截图
- 右键：框选 / 全屏 / 退出

## 编辑器

画笔、矩形、箭头、打码（马赛克）、文字、撤销（Ctrl+Z）、保存（Ctrl+S）。

## 保存位置

默认：桌面 `截图_YYYYMMDD_HHMMSS.png`
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: add README"
```
