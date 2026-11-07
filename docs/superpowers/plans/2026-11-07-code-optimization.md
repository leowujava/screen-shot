# 代码优化实施计划（性能 / 去重 / 健壮性）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 消除画笔每帧整图 4× 超采样与 DIB 编码 Python 循环两大性能热点，去掉 OCR/工具函数的重复实现，修复桌面路径/同秒覆盖/中文字体/复制错误吞没四个健壮性问题。

**Architecture:** 全部改动落在 5 个既有文件内，不新增模块。`drawing.py` 成为共享绘制/工具函数唯一实现处；`capture.py` 承接剪贴板与保存路径逻辑；`editor.py` 退化为对 `drawing` 的 re-export + 编辑器 UI。按 G1→G2→G3→G4 分 4 个 commit 提交（方案 A，用户已确认）。

**Tech Stack:** Python 3.14、Pillow、mss、pywin32、tkinter、pynput、pystray、pytest

**Spec:** `docs/superpowers/specs/2026-11-07-code-optimization-design.md`

## Global Constraints

- Windows-only 项目，可自由使用 `ctypes.windll` / win32 API
- 既有测试必须保持绿色；`from editor import ocr_result_to_text` 等既有导入路径不得破坏（re-export 兼容）；**既有测试不得修改**（只允许新增测试）
- 不改变 `Selector` / `Editor` 构造函数与回调签名（app.py 依赖）
- 画笔局部超采样必须保持与整图超采样一致的视觉效果（`tests/test_drawing.py` 容差判定为验收基线）
- CI（Python 3.14 + `python -m pytest -q`）必须通过
- 按 G1→G2→G3→G4 分 4 个 commit，每组完成后跑全量测试再提交
- 测试命令：仓库根目录、用项目内已建好的 `.venv`（依赖 = requirements 去掉 onnxruntime/rapidocr）：`.\.venv\Scripts\python -m pytest ...`

## 涉及文件

| 文件 | 角色 | 被哪些任务改动 |
|---|---|---|
| `drawing.py` | 共享绘制 + OCR + 工具函数（唯一实现处） | T1, T4, T6, T9 |
| `capture.py` | 截屏 / 剪贴板 / 保存路径 | T2, T5, T7, T8 |
| `editor.py` | 编辑器 UI，re-export drawing | T3, T4, T5, T10 |
| `selector.py` | 选区 + 标注 UI | T4, T5, T6, T10 |
| `app.py` | 托盘 / 快捷键入口 | T10 |
| `tests/test_drawing.py` / `tests/test_capture.py` / `tests/test_selector.py` / `tests/test_editor.py` / `tests/test_app.py` | 新增测试 | 各对应任务 |

## Review Focus

spec 隐含但常规测试不会覆盖、最可能咬到真实用户的输入/失败模式：

1. **画笔线段触及选区图边缘**（端点在图像边界上）→ 局部超采样包围盒被裁剪到图像内，不越界、无黑边、与旧整图超采样逐像素一致 → 由 Task 1 `test_draw_brush_matches_full_supersample` 钉住（120×120 图上线段伸到 (2,60)/(118,118) 边缘）
2. **中文系统 Windows**（桌面文件夹名是 `桌面` 而非 `Desktop`）→ 全屏截图保存到真实桌面，不 FileNotFoundError → 由 Task 7 `test_get_desktop_path_from_shell32` 钉住（mock 返回 `C:\Users\w\桌面`）
3. **同一秒内连续两次截图**（全屏后紧跟框选）→ 第二次保存不覆盖第一张 → 由 Task 8 `test_save_image_avoids_overwrite` 钉住
4. **文字工具输入中文** → 渲染为真实字形而不是豆腐字 → 由 Task 9 `test_draw_text_renders_cjk` 钉住
5. **复制失败**（剪贴板被占用 / OpenClipboard 失败）→ 托盘"复制截图"路径弹出错误对话框，不崩溃也不静默吞掉 → 由 Task 10 `test_do_copy_capture_reports_clipboard_failure` 钉住

---

### Task 1: 画笔局部超采样（G1）

**Files:**
- Modify: `drawing.py`（`_supersample_inplace` 之后新增 `_supersample_local`；重写 `draw_brush`，原 188-199 行）
- Test: `tests/test_drawing.py`（新增 3 个测试）

**Interfaces:**
- Consumes: 无
- Produces: `draw_brush(img, points, color, width)` —— 签名不变，原地修改返回 img；内部 `_supersample_local(img, x0, y0, x1, y1, draw_fn, scale=4)`，`draw_fn(d, s)` 以**局部层坐标**（图像坐标减去包围盒原点，再 ×s）绘制

- [ ] **Step 1: 写测试**（加到 `tests/test_drawing.py` 末尾）

```python
import time


def _full_supersample_reference(img, points, color, width, scale=4):
    """旧整图 4x 超采样的参考实现，用于逐像素对拍。"""
    from PIL import ImageDraw
    layer = Image.new("RGBA", (img.width * scale, img.height * scale), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    if len(points) == 1:
        d.ellipse([(points[0][0] - width / 2) * scale, (points[0][1] - width / 2) * scale,
                   (points[0][0] + width / 2) * scale, (points[0][1] + width / 2) * scale], fill=color)
    else:
        d.line([(x * scale, y * scale) for x, y in points], fill=color,
               width=max(1, int(width * scale)), joint="curve")
    small = layer.resize((img.width, img.height), Image.LANCZOS)
    out = img.convert("RGBA")
    out.alpha_composite(small)
    return out.convert(img.mode)


def test_draw_brush_matches_full_supersample():
    """局部超采样必须与整图超采样逐像素一致（含触及图像边缘的线段）。"""
    img = Image.new("RGB", (120, 120), (200, 210, 220))
    points = [(2, 60), (60, 2), (118, 118)]  # 触及上/左/右/下边缘
    expected = _full_supersample_reference(img, points, "#ff0000", 3)
    result = img.copy()
    draw_brush(result, points, "#ff0000", 3)
    assert list(result.getdata()) == list(expected.getdata())


def test_draw_brush_single_point_matches_reference():
    img = Image.new("RGB", (60, 60), (10, 20, 30))
    expected = _full_supersample_reference(img, [(30, 30)], "#00ff00", 5)
    result = img.copy()
    draw_brush(result, [(30, 30)], "#00ff00", 5)
    assert list(result.getdata()) == list(expected.getdata())


def test_draw_brush_perf_large_image():
    """2000x1200 图上 30 段画笔应在 1 秒内完成（优化前每帧整图 4x ≈ 数十秒）。"""
    big = Image.new("RGB", (2000, 1200), (255, 255, 255))
    last = (100, 100)
    t0 = time.perf_counter()
    for i in range(30):
        p = (100 + i * 8, 100 + i * 5)
        draw_brush(big, [last, p], "#ff0000", 3)
        last = p
    elapsed = time.perf_counter() - t0
    assert elapsed < 1.0, f"30 段画笔耗时 {elapsed:.2f}s（阈值 1.0s）"
```

- [ ] **Step 2: 运行确认性能测试失败、一致性测试通过**

Run: `.\.venv\Scripts\python -m pytest tests/test_drawing.py -v`
Expected: `test_draw_brush_perf_large_image` FAIL（耗时超 1s）；两个 `*_matches_*` 测试 PASS（当前实现就是参考实现，一致性测试是守卫）

- [ ] **Step 3: 在 `drawing.py` 实现局部超采样**

在 `_supersample_inplace` 之后新增：

```python
def _supersample_local(img, x0, y0, x1, y1, draw_fn, scale=4):
    """局部超采样：只在 [x0,y0,x1,y1] 包围盒范围内做 scale 倍超采样。

    draw_fn(d, s) 接收局部层 Draw 对象与缩放系数，以局部层坐标
    （图像坐标减去 (x0, y0) 原点，再 × s）绘制。包围盒可越界，
    越界部分绘制后被丢弃。把结果合成回 img（保持原对象）。
    """
    W, H = img.size
    bw, bh = x1 - x0, y1 - y0
    if bw <= 0 or bh <= 0:
        return img
    layer = Image.new("RGBA", (bw * scale, bh * scale), (0, 0, 0, 0))
    draw_fn(ImageDraw.Draw(layer), scale)
    small = layer.resize((bw, bh), Image.LANCZOS)
    # small 覆盖的图像实际区域（与图像求交）
    cx0, cy0 = max(0, x0), max(0, y0)
    cx1, cy1 = min(W, x1), min(H, y1)
    if cx1 <= cx0 or cy1 <= cy0:
        return img
    crop = small.crop((cx0 - x0, cy0 - y0, cx1 - x0, cy1 - y0))
    patch = img.crop((cx0, cy0, cx1, cy1)).convert("RGBA")
    patch.alpha_composite(crop)
    img.paste(patch.convert(img.mode), (cx0, cy0))
    return img
```

重写 `draw_brush`（替换原实现，保持 docstring 风格）：

```python
def draw_brush(img, points, color, width):
    """在 img 上画折线画笔（4x 超采样抗锯齿，圆头线帽，局部作用域）。points 为 [(x, y), ...]。"""
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    margin = max(2 * width, 8)  # ≥ LANCZOS 重采样窗口(3px) 与线宽，保证与整图超采样一致
    x0, y0 = int(min(xs)) - margin, int(min(ys)) - margin
    x1, y1 = int(max(xs)) + margin, int(max(ys)) + margin

    def draw(d, s):
        if len(points) == 1:
            px, py = points[0]
            d.ellipse([(px - x0 - width / 2) * s, (py - y0 - width / 2) * s,
                       (px - x0 + width / 2) * s, (py - y0 + width / 2) * s], fill=color)
        else:
            d.line([((px - x0) * s, (py - y0) * s) for px, py in points], fill=color,
                   width=max(1, int(width * s)), joint="curve")

    return _supersample_local(img, x0, y0, x1, y1, draw)
```

`draw_arrow` / `draw_rect` / `draw_ellipse` 不动（只在松开鼠标时调用一次，非热路径）。

- [ ] **Step 4: 运行测试确认全部通过**

Run: `.\.venv\Scripts\python -m pytest tests/test_drawing.py -v`
Expected: 全部 PASS（含既有的 `test_draw_brush_draws_polylines`）

- [ ] **Step 5: 跑全量测试并提交（G1 commit）**

Run: `.\.venv\Scripts\python -m pytest -q`
Expected: 全部 PASS（基线 65 + 新增 3）

```bash
git add drawing.py tests/test_drawing.py
git commit -m "perf: 画笔改为局部超采样，消除每帧整图 4x 分配"
```

---

### Task 2: DIB 行序翻转改用 C 级 transpose（G2）

**Files:**
- Modify: `capture.py` `_build_dib`（44-81 行）
- Test: `tests/test_capture.py`（新增 2 个测试）

**Interfaces:**
- Consumes: 无
- Produces: `_build_dib(img) -> bytes`（签名与输出格式不变）

- [ ] **Step 1: 写字节对拍测试**（加到 `tests/test_capture.py` 末尾；文件顶部 import 行补上 `_build_dib`）

```python
def _reference_dib(img):
    """旧逐行 Python 翻转的参考实现，用于字节对拍。"""
    import struct
    im = img.convert("RGB") if img.mode != "RGB" else img
    w, h = im.size
    pixels = im.tobytes("raw", "BGRX")
    row = w * 4
    pixels_bu = b"".join(pixels[i * row:(i + 1) * row] for i in range(h - 1, -1, -1))
    header = struct.pack("<IiiHHIIiiII", 40, w, h, 1, 32, 0,
                         len(pixels_bu), 0, 0, 0, 0)
    return header + pixels_bu


def test_build_dib_matches_row_flip_reference():
    img = Image.new("RGB", (3, 2), (0, 0, 0))
    for x in range(3):
        for y in range(2):
            img.putpixel((x, y), (x * 80, y * 120, (x + y) * 40))
    assert _build_dib(img) == _reference_dib(img)


def test_build_dib_non_rgb_input():
    import struct
    img = Image.new("RGBA", (2, 2), (10, 20, 30, 255))
    dib = _build_dib(img)
    assert len(dib) == 40 + 2 * 2 * 4
    w, h = struct.unpack("<ii", dib[4:12])
    assert (w, h) == (2, 2)
```

- [ ] **Step 2: 运行确认测试通过（对拍是守卫，先于重构）**

Run: `.\.venv\Scripts\python -m pytest tests/test_capture.py -v`
Expected: 全部 PASS（当前实现与参考一致）

- [ ] **Step 3: 重构 `_build_dib`**

把 `pixels = img.tobytes("raw", "BGRX")` 与后面的 3 行翻转循环（`row = w * 4` / `rows = [...]` / `rows.reverse()` / `pixels_bu = b"".join(rows)`）替换为一行：

```python
    # C 级整块翻转（bottom-up），替代原 Python 逐行切片循环
    pixels_bu = img.transpose(Image.FLIP_TOP_BOTTOM).tobytes("raw", "BGRX")
```

BITMAPINFOHEADER 构造与函数 docstring 保持不变（docstring 中"逐行翻转"一句改为"transpose 翻转"）。

- [ ] **Step 4: 运行测试确认通过**

Run: `.\.venv\Scripts\python -m pytest tests/test_capture.py -v`
Expected: 全部 PASS（对拍证明字节级一致）

- [ ] **Step 5: 跑全量测试并提交（G2 commit）**

Run: `.\.venv\Scripts\python -m pytest -q`
Expected: 全部 PASS

```bash
git add capture.py tests/test_capture.py
git commit -m "perf: DIB 行序翻转改用 C 级 transpose，去掉 Python 循环"
```

---

### Task 3: OCR 去重，editor 改为 re-export（G3a）

**Files:**
- Modify: `editor.py`（删除 16-118 行的本地 OCR 实现；扩展 drawing import；`Editor._ocr` 内 846 行调用点改名）
- Test: `tests/test_editor.py`（新增 1 个测试）

**Interfaces:**
- Consumes: `drawing.get_ocr_engine()`, `drawing.ocr_result_to_text(result)`
- Produces: `editor` 模块继续暴露 `ocr_result_to_text`（re-export，`test_ocr.py` 的 `from editor import ocr_result_to_text` 不受影响）；editor 内不再有 `_OCR_ENGINE` / `_OCR_ENGINE_READY` / `_get_ocr_engine`

- [ ] **Step 1: 写失败测试**（加到 `tests/test_editor.py` 末尾）

```python
def test_ocr_reexported_from_drawing():
    """editor 的 OCR 逻辑必须来自 drawing 的唯一实现（去重）。"""
    import editor, drawing
    assert editor.ocr_result_to_text is drawing.ocr_result_to_text
```

- [ ] **Step 2: 运行确认失败**

Run: `.\.venv\Scripts\python -m pytest tests/test_editor.py::test_ocr_reexported_from_drawing -v`
Expected: FAIL（两个不同的函数对象，`is` 不成立）

- [ ] **Step 3: 重构 `editor.py`**

- 扩展 drawing 导入为：
  `from drawing import (get_ocr_engine, ocr_result_to_text, mosaic, UndoStack,`
  `                     draw_arrow, draw_rect, draw_ellipse, draw_brush, draw_text)`
- 删除 16-118 行整段（OCR 注释、`_OCR_ENGINE = None`、`_OCR_ENGINE_READY = False`、`_get_ocr_engine`、`ocr_result_to_text` 本地定义）
- `Editor._ocr` 中 `engine = _get_ocr_engine()` → `engine = get_ocr_engine()`

- [ ] **Step 4: 运行测试确认通过**

Run: `.\.venv\Scripts\python -m pytest tests/test_editor.py tests/test_ocr.py -v`
Expected: 全部 PASS

> 本任务不提交（G3 组在 Task 6 末尾统一提交）。

---

### Task 4: `alt_pressed` 移入 drawing（G3b）

**Files:**
- Modify: `drawing.py`（新增模块级 `alt_pressed`；补 `import sys`）
- Modify: `selector.py`（删除 1072-1083 行静态方法；drawing import 行加 `alt_pressed`；956、1025 行两处调用点 `self._alt_pressed(event)` → `alt_pressed(event)`）
- Modify: `editor.py`（删除 641-657 行静态方法；drawing import 加 `alt_pressed`；685、731 行两处调用点同理）
- Test: `tests/test_drawing.py`（新增 1 个测试）

**Interfaces:**
- Produces: `drawing.alt_pressed(event=None) -> bool`（Windows 用 `GetAsyncKeyState(VK_MENU)`，其余平台回退 `event.state & 0x20000`；`event=None` 时 False）

- [ ] **Step 1: 写失败测试**（加到 `tests/test_drawing.py`）

```python
def test_alt_pressed_state_fallback(monkeypatch):
    import drawing, sys

    class Ev:
        def __init__(self, state):
            self.state = state

    monkeypatch.setattr(sys, "platform", "linux")
    assert drawing.alt_pressed(Ev(0x20000)) is True
    assert drawing.alt_pressed(Ev(0)) is False
    assert drawing.alt_pressed(None) is False
```

- [ ] **Step 2: 运行确认失败**

Run: `.\.venv\Scripts\python -m pytest tests/test_drawing.py::test_alt_pressed_state_fallback -v`
Expected: FAIL（`AttributeError: module 'drawing' has no attribute 'alt_pressed'`）

- [ ] **Step 3: 迁移**

- `drawing.py` 顶部补 `import sys`；新增模块级函数 `alt_pressed(event=None)`，函数体与 selector.py 现有静态方法完全相同（Win32 分支 + `state = getattr(event, "state", 0) or 0` 回退）
- `selector.py`：删除静态方法；`from drawing import (...)` 列表加 `alt_pressed`；两处 `self._alt_pressed(event)` 改 `alt_pressed(event)`
- `editor.py`：同样处理（两处调用点）

- [ ] **Step 4: 运行测试确认通过**

Run: `.\.venv\Scripts\python -m pytest tests/test_drawing.py tests/test_selector.py -v`
Expected: 全部 PASS

> 本任务不提交（G3 组统一提交）。

---

### Task 5: `copy_text_to_clipboard` 收敛到 capture（G3c）

**Files:**
- Modify: `capture.py`（新增 `copy_text_to_clipboard`）
- Modify: `selector.py` `_show_ocr_result`（删除内嵌 `_copy_to_clipboard(s)`，两处调用改新函数；**必须用函数内 import**，见 Step 3 说明）
- Modify: `editor.py`（顶部 `from capture import copy_to_clipboard` 扩展为 `from capture import copy_to_clipboard, copy_text_to_clipboard`；`_show_ocr_result` 的 `copy_all` 改新函数）
- Test: `tests/test_capture.py`（新增 2 个测试）

**Interfaces:**
- Produces: `capture.copy_text_to_clipboard(text: str) -> bool`（CF_UNICODETEXT=13；成功 True，任何异常 False）

- [ ] **Step 1: 写失败测试**（加到 `tests/test_capture.py`；import 行补 `copy_text_to_clipboard`）

```python
def test_copy_text_to_clipboard_sets_unicode_text():
    from unittest.mock import patch
    with patch("win32clipboard.OpenClipboard"), \
         patch("win32clipboard.EmptyClipboard"), \
         patch("win32clipboard.SetClipboardData") as scd, \
         patch("win32clipboard.CloseClipboard"):
        assert copy_text_to_clipboard("你好 hello") is True
    scd.assert_called_once_with(13, "你好 hello")  # CF_UNICODETEXT


def test_copy_text_to_clipboard_returns_false_on_error():
    from unittest.mock import patch
    with patch("win32clipboard.OpenClipboard", side_effect=OSError("clipboard busy")):
        assert copy_text_to_clipboard("x") is False
```

- [ ] **Step 2: 运行确认失败**

Run: `.\.venv\Scripts\python -m pytest tests/test_capture.py -k copy_text -v`
Expected: FAIL（ImportError / AttributeError：函数不存在）

- [ ] **Step 3: 实现与替换**

`capture.py` 新增（放在 `copy_to_clipboard` 之后）：

```python
def copy_text_to_clipboard(text):
    """把文本复制到系统剪贴板（CF_UNICODETEXT）。成功返回 True，失败返回 False。"""
    import win32clipboard
    try:
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(13, text)  # CF_UNICODETEXT
        finally:
            win32clipboard.CloseClipboard()
        return True
    except Exception:
        return False
```

- `selector.py` `_show_ocr_result`：删除内嵌 `_copy_to_clipboard(s)` 函数；`copy_all` / `copy_selected` 中的调用改为 `copy_text_to_clipboard(...)`。**必须在 `_show_ocr_result` 函数体内** `from capture import copy_text_to_clipboard`（不能放模块顶部：`tests/test_selector.py` 的 fake_root fixture 用只有 `capture_fullscreen` 的假 `capture` 模块替换了 `sys.modules["capture"]`，顶层 import 会直接炸掉整个测试文件）
- `editor.py`：顶部 import 扩展；`copy_all` 改为 `if copy_text_to_clipboard(text): dlg.destroy()`

- [ ] **Step 4: 运行测试确认通过**

Run: `.\.venv\Scripts\python -m pytest tests/test_capture.py tests/test_selector.py tests/test_editor.py -v`
Expected: 全部 PASS

> 本任务不提交（G3 组统一提交）。

---

### Task 6: `_to_virtual_region` 提取 + UndoStack 改 deque + 小清理（G3d，G3 收尾提交）

**Files:**
- Modify: `selector.py`（`_confirm` / `_copy` / `_save` 改为调用新 `_to_virtual_region`；`_show_ocr_result` 中 `except (tk.TclError, Exception)` → `except Exception`（约 1290 行）；`_input_text_dialog` 删除第二个 `top.grab_set()`（约 1160 行））
- Modify: `drawing.py` `UndoStack`（list → `collections.deque(maxlen=...)`）
- Test: `tests/test_selector.py`（新增 1 个测试）

**Interfaces:**
- Produces: `Selector._to_virtual_region() -> tuple[left, top, width, height] | None`（`self._region` 为 None 时返回 None；坐标换算逻辑与现 `_confirm` 内完全相同：`int(l * _scale_x) + self.x` 等）

- [ ] **Step 1: 写失败测试**（加到 `tests/test_selector.py`，放在 `test_virtual_coord_conversion` 之后，复用其构造方式）

```python
def test_to_virtual_region(fake_root):
    sel = Selector(x=-1920, y=0, width=3840, height=1080, full_img=None,
                   on_select=lambda r, i: None,
                   on_copy=lambda r, i: None,
                   on_save=lambda r, i: None)
    assert sel._to_virtual_region() is None
    sel._region = (100, 50, 300, 150)
    # scale 1.0：canvas 坐标直接 + x 偏移
    assert sel._to_virtual_region() == (-1820, 50, 200, 100)
    # scale 2.0：先乘 scale 再加偏移
    sel._scale_x = 2.0
    sel._scale_y = 2.0
    assert sel._to_virtual_region() == (-1720, 100, 400, 200)
```

- [ ] **Step 2: 运行确认失败**

Run: `.\.venv\Scripts\python -m pytest tests/test_selector.py::test_to_virtual_region -v`
Expected: FAIL（`AttributeError: 'Selector' object has no attribute '_to_virtual_region'`）

- [ ] **Step 3: 实现与清理**

- `selector.py` 新增（放在 `_calc_region` 附近，坐标/区域计算小节）：

```python
def _to_virtual_region(self):
    """当前选区 → mss 虚拟桌面坐标 (left, top, width, height)；无选区返回 None。"""
    if self._region is None:
        return None
    l, t, r, b = self._region
    vx_l = int(l * self._scale_x)
    vx_t = int(t * self._scale_y)
    vx_r = int(r * self._scale_x)
    vx_b = int(b * self._scale_y)
    return (vx_l + self.x, vx_t + self.y, vx_r - vx_l, vx_b - vx_t)
```

- `_confirm` / `_copy` / `_save` 三个方法体改为：

```python
def _confirm(self):
    region = self._to_virtual_region()
    if region is None:
        self._cancel()
        return
    self.result = region
    self._finish(lambda: self.on_select(region, self._region_img))
```

（`_copy` / `_save` 同理，回调分别为 `self.on_copy` / `self.on_save`）
- `drawing.py` `UndoStack` 改 deque：

```python
from collections import deque

class UndoStack:
    def __init__(self, maxlen=10):
        self._stack = deque(maxlen=maxlen)

    def push(self, item):
        self._stack.append(item)

    def pop(self):
        if self._stack:
            return self._stack.pop()
        return None

    def clear(self):
        self._stack.clear()

    def __len__(self):
        return len(self._stack)
```

（原 `self.maxlen` 属性无外部引用，删除；`test_selector.py` 中 `sel.undo._stack == []` 对空 deque 成立）
- 小清理：`except (tk.TclError, Exception)` → `except Exception`；删 `_input_text_dialog` 里第二个 `top.grab_set()`

- [ ] **Step 4: 运行全量测试 + 去重核验**

Run: `.\.venv\Scripts\python -m pytest -q`
Expected: 全部 PASS

核验（都应只出现 1 处定义）：
Run: `git grep -n "def ocr_result_to_text"` → 只有 `drawing.py`
Run: `git grep -n "def _alt_pressed\|def alt_pressed"` → 只有 `drawing.py`

- [ ] **Step 5: 提交（G3 commit）**

```bash
git add drawing.py editor.py selector.py tests/test_selector.py tests/test_editor.py tests/test_capture.py
git commit -m "refactor: 去重 OCR/alt_pressed/文字剪贴板/选区计算，UndoStack 改用 deque"
```

---

### Task 7: 真实桌面路径（G4a）

**Files:**
- Modify: `capture.py`（新增 `get_desktop_path`；`default_save_path` 改用它）
- Test: `tests/test_capture.py`（新增 3 个测试）

**Interfaces:**
- Produces: `capture.get_desktop_path() -> str`（Win32 `SHGetFolderPathW(None, 0, None, 0x20, buf)`，CSIDL_DESKTOP=0、SHGFP_TYPE_CURRENT=0x20，buf 为 `create_unicode_buffer(260)`；HRESULT≠0 或异常时回退 `os.path.join(os.path.expanduser("~"), "Desktop")`）

- [ ] **Step 1: 写失败测试**（加到 `tests/test_capture.py`；import 行补 `get_desktop_path, default_save_path`；顶部补 `import os`）

```python
def test_get_desktop_path_from_shell32(monkeypatch):
    import ctypes
    def fake_shget(hwnd, csidl, token, flags, buf):
        buf[0:15] = "C:\\Users\\w\\桌面"
        return 0
    monkeypatch.setattr(ctypes.windll.shell32, "SHGetFolderPathW", fake_shget)
    assert get_desktop_path() == "C:\\Users\\w\\桌面"


def test_get_desktop_path_fallback(monkeypatch):
    import ctypes
    monkeypatch.setattr(ctypes.windll.shell32, "SHGetFolderPathW", lambda *a: 1)  # E_FAIL
    assert get_desktop_path() == os.path.join(os.path.expanduser("~"), "Desktop")


def test_default_save_path_uses_desktop(monkeypatch):
    import capture
    monkeypatch.setattr(capture, "get_desktop_path", lambda: "D:\\桌面")
    p = default_save_path()
    assert p.startswith("D:\\桌面\\截图_")
```

- [ ] **Step 2: 运行确认失败**

Run: `.\.venv\Scripts\python -m pytest tests/test_capture.py -k "desktop or save_path_uses" -v`
Expected: FAIL（`get_desktop_path` 不存在）

- [ ] **Step 3: 实现**

```python
def get_desktop_path():
    """解析真实桌面路径（中文系统返回 桌面 目录）；失败回退 ~\\Desktop。"""
    import ctypes
    buf = ctypes.create_unicode_buffer(260)
    try:
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0, None, 0x20, buf) == 0:
            return buf.value
    except Exception:
        pass
    return os.path.join(os.path.expanduser("~"), "Desktop")
```

`default_save_path` 中 `desktop = os.path.join(os.path.expanduser("~"), "Desktop")` → `desktop = get_desktop_path()`

- [ ] **Step 4: 运行测试确认通过**

Run: `.\.venv\Scripts\python -m pytest tests/test_capture.py -v`
Expected: 全部 PASS

> 本任务不提交（G4 组在 Task 10 末尾统一提交）。

---

### Task 8: `save_image` 防同秒覆盖（G4b）

**Files:**
- Modify: `capture.py` `save_image`（37-41 行）
- Test: `tests/test_capture.py`（新增 1 个测试）

**Interfaces:**
- Consumes: `default_save_path()`（Task 7 改造后）
- Produces: `save_image(img, path=None) -> str`（目标已存在时在扩展名前追加 `_1`、`_2`…；不冲突时直接用原路径）

- [ ] **Step 1: 写失败测试**

```python
def test_save_image_avoids_overwrite(tmp_path):
    img = Image.new("RGB", (2, 2), (255, 0, 0))
    path = tmp_path / "截图_20260101_120000.png"
    path.write_bytes(b"ORIGINAL")
    p1 = save_image(img, str(path))
    assert p1 == str(tmp_path / "截图_20260101_120000_1.png")
    assert path.read_bytes() == b"ORIGINAL"  # 原文件未被覆盖
    p2 = save_image(img, str(path))
    assert p2 == str(tmp_path / "截图_20260101_120000_2.png")
    # 无冲突时直接保存原路径
    p3 = save_image(img, str(tmp_path / "free.png"))
    assert p3 == str(tmp_path / "free.png")
```

- [ ] **Step 2: 运行确认失败**

Run: `.\.venv\Scripts\python -m pytest tests/test_capture.py::test_save_image_avoids_overwrite -v`
Expected: FAIL（p1 等于原 path，原文件被覆盖）

- [ ] **Step 3: 实现**

```python
def save_image(img, path=None):
    """保存图片，返回实际保存路径。目标已存在时追加 _1、_2... 后缀防覆盖。失败抛异常。"""
    path = path or default_save_path()
    if os.path.exists(path):
        base, ext = os.path.splitext(path)
        n = 1
        while os.path.exists(f"{base}_{n}{ext}"):
            n += 1
        path = f"{base}_{n}{ext}"
    img.save(path)
    return path
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.\.venv\Scripts\python -m pytest tests/test_capture.py -v`
Expected: 全部 PASS

> 本任务不提交（G4 组统一提交）。

---

### Task 9: `draw_text` 中文字体链（G4c）

**Files:**
- Modify: `drawing.py`（顶部 import 加 `ImageFont`；新增 `_FONT_CANDIDATES` / `_font_cache` / `_load_font`；`draw_text` 改用 `_load_font`，删除函数内 try/except 与局部 import）
- Test: `tests/test_drawing.py`（新增 3 个测试）

**Interfaces:**
- Produces: `drawing._load_font(size: int) -> ImageFont`（按 `("msyh.ttc", "msyh.ttf", "simhei.ttf", "arial.ttf")` 顺序尝试，按 `(文件名, size)` 缓存；全部失败返回 `ImageFont.load_default()`）

- [ ] **Step 1: 写失败测试**（加到 `tests/test_drawing.py`；顶部补 `import sys, pytest, os`）

```python
@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
def test_load_font_prefers_cjk_font():
    from PIL import ImageFont
    import drawing
    font = drawing._load_font(16)
    assert isinstance(font, ImageFont.FreeTypeFont)
    assert os.path.basename(font.path) in ("msyh.ttc", "msyh.ttf", "simhei.ttf", "arial.ttf")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
def test_draw_text_renders_cjk():
    """中文标注必须渲染为真实字形（足够多的非背景像素），而非豆腐字。"""
    img = Image.new("RGB", (160, 50), (255, 255, 255))
    draw_text(img, (8, 12), "中文标注", "#000000", font_size=20)
    dark = sum(1 for x in range(8, 130) for y in range(8, 45)
               if img.getpixel((x, y)) != (255, 255, 255))
    assert dark > 50


def test_load_font_fallback_when_truetype_fails(monkeypatch):
    from PIL import ImageFont
    import drawing

    def boom(*a, **k):
        raise OSError("no font")

    monkeypatch.setattr(ImageFont, "truetype", boom)
    font = drawing._load_font(17)  # 用未缓存的 size，避免命中前面测试的缓存
    assert font is not None  # 回退 load_default，不抛异常
```

- [ ] **Step 2: 运行确认失败**

Run: `.\.venv\Scripts\python -m pytest tests/test_drawing.py -k "load_font or cjk" -v`
Expected: FAIL（`_load_font` 不存在）

- [ ] **Step 3: 实现**

`drawing.py` 顶部 import 改为 `from PIL import Image, ImageDraw, ImageFilter, ImageFont`；新增：

```python
# 标注字体候选（优先支持 CJK 的字形，Windows 自带微软雅黑/黑体）
_FONT_CANDIDATES = ("msyh.ttc", "msyh.ttf", "simhei.ttf", "arial.ttf")
_font_cache = {}


def _load_font(size):
    """加载标注字体：按候选顺序尝试 truetype，全部失败回退 load_default()。

    按 (文件名, size) 缓存，避免每帧重复加载。
    """
    for name in _FONT_CANDIDATES:
        key = (name, size)
        if key in _font_cache:
            return _font_cache[key]
        try:
            font = ImageFont.truetype(name, size)
        except Exception:
            continue
        _font_cache[key] = font
        return font
    return ImageFont.load_default()
```

`draw_text` 改为：

```python
def draw_text(img, pos, text, color, font_size):
    """在 img 上画文字。pos 为 (x, y)，font_size 为 int。"""
    d = ImageDraw.Draw(img)
    d.text(pos, text, fill=color, font=_load_font(font_size))
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.\.venv\Scripts\python -m pytest tests/test_drawing.py -v`
Expected: 全部 PASS（既有 `test_draw_text_draws_text` 不回归）

> 本任务不提交（G4 组统一提交）。

---

### Task 10: 复制截图错误提示 + OCR 文案（G4d，G4 收尾提交）

**Files:**
- Modify: `app.py` `do_copy_capture`（123-131 行）
- Modify: `selector.py`（约 1227-1229 行 OCR 缺失提示文案）
- Modify: `editor.py`（约 848-850 行 OCR 缺失提示文案）
- Test: `tests/test_app.py`（新增 2 个测试）

**Interfaces:**
- Consumes: `app._safe_copy_to_clipboard(img)`（已存在）
- Produces: `do_copy_capture()` 行为变化——复制失败时弹"复制失败"错误对话框（与全屏截图路径一致），不再静默

- [ ] **Step 1: 写失败测试**（加到 `tests/test_app.py`）

```python
def test_do_copy_capture_uses_safe_copy():
    """复制截图应走 _safe_copy_to_clipboard（失败有提示），不再裸吞异常。"""
    import threading
    from PIL import Image
    import app

    done = threading.Event()

    def fake_capture():
        done.set()
        return Image.new("RGB", (10, 10))

    with patch.object(app, "capture_fullscreen", fake_capture), \
         patch.object(app, "_safe_copy_to_clipboard") as mock_safe:
        app.do_copy_capture()
        assert done.wait(5)
        mock_safe.assert_called_once()


def test_do_copy_capture_reports_clipboard_failure():
    """剪贴板打开失败时，应弹出错误对话框而不是崩溃或静默。"""
    import threading
    from PIL import Image
    import app

    done = threading.Event()

    def fake_capture():
        done.set()
        return Image.new("RGB", (10, 10))

    with patch.object(app, "capture_fullscreen", fake_capture), \
         patch.object(app, "copy_to_clipboard", side_effect=RuntimeError("clipboard busy")), \
         patch("tkinter.messagebox.showerror") as mock_err:
        app.do_copy_capture()
        assert done.wait(5)
    mock_err.assert_called_once()
```

- [ ] **Step 2: 运行确认失败**

Run: `.\.venv\Scripts\python -m pytest tests/test_app.py -v`
Expected: 两个新测试 FAIL（现实现直接调 `copy_to_clipboard` 并 `except: pass`）

- [ ] **Step 3: 实现**

`app.py`：

```python
def do_copy_capture():
    """全屏截图只复制到剪贴板，不保存。耗时操作在工作线程执行。"""
    def worker():
        full = capture_fullscreen()
        _safe_copy_to_clipboard(full)
    threading.Thread(target=worker, daemon=True).start()
```

OCR 缺失提示文案（`selector.py` 与 `editor.py` 两处同步改）：

```
"未安装 OCR 引擎（rapidocr-onnxruntime）。\n请执行：pip install rapidocr-onnxruntime"
→
"未安装 OCR 引擎。\n请执行：pip install rapidocr\n（或：pip install rapidocr-onnxruntime）"
```

- [ ] **Step 4: 运行全量测试**

Run: `.\.venv\Scripts\python -m pytest -q`
Expected: 全部 PASS（基线 65 + 新增测试，0 failed）

- [ ] **Step 5: 提交（G4 commit）并做最终核验**

```bash
git add app.py selector.py editor.py tests/test_app.py tests/test_capture.py tests/test_drawing.py
git commit -m "fix: 真实桌面路径/防覆盖保存/中文字体/复制错误提示"
```

最终核验（对应 spec 约束与 Review Focus）：
- `git log --oneline -4` → 恰好 4 个主题 commit（perf / perf / refactor / fix）
- `.\.venv\Scripts\python -m pytest -q` → 全部 PASS
- `git grep -c "rapidocr-onnxruntime" selector.py editor.py` → 两处文案都提到 `rapidocr` 优先
- 手工冒烟（可选，真实桌面）：`.\.venv\Scripts\python app.py` 起托盘 → 框选截图画一笔中文文字 → 确认字形正常、保存/复制正常

---

## Self-Review（计划作者自查记录）

1. **Spec 覆盖**：G1→T1（含性能测试）、G2→T2（含字节对拍）、G3→T3-T6（OCR/alt_pressed/文字剪贴板/选区计算/deque/小清理）、G4-1→T7、G4-2→T8、G4-3→T9、G4-4→T10、G4-5→T10。无遗漏。
2. **步骤扫描**：每步一个可检验动作；测试步骤含精确断言，代码步骤含精确签名/值，算法步骤（局部超采样、防覆盖后缀）给出实现。无"适当处理"类空话。
3. **类型一致性**：`_to_virtual_region() -> tuple|None`、`copy_text_to_clipboard(text) -> bool`、`alt_pressed(event=None) -> bool`、`_load_font(size) -> ImageFont`、`get_desktop_path() -> str` 在各任务间引用一致。
4. **Review Focus**：5 条输入/失败模式各有钉住它的测试（T1 边缘对拍 / T7 中文桌面 / T8 防覆盖 / T9 CJK 渲染 / T10 剪贴板失败）。
5. **篇幅比例**：计划比 spec 长主要来自测试代码（TDD 必需）；实现代码只给出了签名无法确定的算法体。
