# 工具栏整体美化（SVG 图标 + 统一 token）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把三处工具条（选区 `selector.py` / 编辑 `editor.py` / 托盘悬浮菜单 `app.py`）的 Unicode 字符图标替换为可重着色的 SVG 矢量图标，并抽取统一设计 token 文件 `theme.py`，规范颜色/字体/间距/圆角。

**Architecture:** 新增 `theme.py`（设计 token）+ `icon.py`（SVG→PIL 渲染 + 重着色 + 缓存）+ `assets/icons/*.svg`（16 个线性图标）。三处工具条改为 `tk.Label(image=...)` 图片按钮，hover/选中/禁用态通过切换 `configure(image=)` 实现（不同颜色图标）。圆角只做到内部容器（Canvas + 预渲染圆角 PhotoImage），窗口本身仍直角。

**Tech Stack:** Python 3.14 + tkinter + Pillow + svglib + reportlab + rlPyCairo（SVG 渲染后端，纯 Python，无需系统 cairo 库）。

**Spec:** `docs/superpowers/specs/2026-11-03-toolbar-beautify-design.md`

## Global Constraints

- Python + tkinter，Windows 优先；共享 Tk 根窗口架构不变（所有 GUI 挂同一个 root）。
- 新增依赖：`svglib`、`reportlab`、`rlPyCairo`（**不用** `cairosvg`，本机无 cairo 动态库；`pycairo` wheel 自带 cairo DLL，`rlPyCairo` 依赖 `pycairo` 自动解决）。
- 图标渲染：`svglib.svg2rlg` 解析 SVG → `reportlab.graphics.renderPM.drawToPMCanvas(..., backendFmt='RGBA')` → 从 `canvas._gs.pixBuf` 读 ARGB32 字节流 → `PIL.Image.frombytes(pix, 'raw', 'ARGB')` → 重着色（保留 alpha，RGB 替换为 color）→ `PIL.ImageTk.PhotoImage`。
- **渲染器懒加载**：`icon.py` 顶层不 import svglib/reportlab；`render()` 首次调用时才 import。renderer 不可用（import 失败）时返回 1×1 透明 `PhotoImage` 并记 `icon._RENDERER_AVAILABLE = False`，**不抛异常**。这保证 `test_selector.py` 等不 mock `icon` 的测试仍通过（icon 渲染退化为占位，但 `configure(image=)` 调用仍发生，测试断言不变）。
- 缓存 key = `(name, color, size)`，命中零渲染。
- 不改任何交互逻辑：工具切换、颜色行、粗细、撤销、OCR、复制/保存/确认/取消、工具条跟随/拖动全部保持。
- 不改托盘图标（`_make_tray_icon_image`）。不改画布/选区（遮罩、手柄、十字准星）。
- 图标 18×18，按钮内边距 水平 8 / 垂直 6；操作按钮（图标+文字）文字与图标间距 4。
- `TB_RADIUS=8`（容器圆角）、`BTN_RADIUS=6`（按钮圆角，本期仅 token，不实现按钮级圆角）。

## Review Focus

spec 描述了用户会遇到的场景，但测试不覆盖的，最可能咬人的 5 类：

1. **高 DPI 下图标清晰度**：18×18 SVG 在 150% 缩放下应清晰不糊。测试不覆盖（无真实 Tk 窗口渲染）；Task 8 的 `manual_selector.py` 手动验证 + 高 DPI 截图对比。
2. **renderer 缺失时 UI 不崩**：`icon.render` 在 svglib/reportlab 未安装时返回 1×1 透明图，不抛异常。Task 3 的 `test_icon_missing_renderer` 覆盖（mock import 失败）。
3. **缓存命中零渲染**：同一 (name,color,size) 多次调用只渲染一次。Task 3 的 `test_icon_cache_hit` 覆盖。
4. **圆角容器在窗口 resize 时重算**：`<Configure>` 事件触发圆角 PhotoImage 重生成；拖动跟随时每帧重绘（仅画一张矩形图，开销可接受）。Task 4-6 的改造里 `<Configure>` 绑定；Task 8 手动验证拖动时圆角不闪烁。
5. **深色/浅色两套 token 颜色一致**：`LIGHT`/`DARK` 的 key 集合必须完整，颜色均为合法 hex。Task 1 的 `test_theme` 覆盖。

---

### Task 1: `theme.py` 设计 token + 测试

**Files:**
- Create: `theme.py`
- Test: `tests/test_theme.py`

**Interfaces:**
- Consumes: 无（叶子模块）。
- Produces: `theme.FONT`, `theme.FONT_ICON`, `theme.ICON_SIZE`(18), `theme.BTN_PAD_X`(8), `theme.BTN_PAD_Y`(6), `theme.TB_RADIUS`(8), `theme.BTN_RADIUS`(6), `theme.SEP_W`(1), `theme.SEP_H`(18), `theme.LIGHT`（dict, 10 键）, `theme.DARK`（dict, 14 键）, `theme.ACCENT`("#4a90e2"), `theme.PALETTE`（list, 10 色）, `theme.rounded_rect_photo(master, w, h, radius, fill, outline)` → `PIL.ImageTk.PhotoImage`。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_theme.py
import re
import theme


def _is_hex(c):
    return isinstance(c, str) and re.fullmatch(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})", c) is not None


def test_theme_light_keys():
    required = {"surface", "border", "fg", "hover", "hover_fg",
                "active", "active_fg", "disabled", "sep"}
    assert required <= set(theme.LIGHT), f"LIGHT 缺少键: {required - set(theme.LIGHT)}"


def test_theme_dark_keys():
    required = {"surface", "border", "fg", "hover", "hover_fg", "btn", "btn_fg",
                "btn_hover", "active", "active_fg", "primary", "primary_hover",
                "sep", "confirm", "cancel"}
    assert required <= set(theme.DARK), f"DARK 缺少键: {required - set(theme.DARK)}"


def test_theme_hex_colors():
    for name, palette in (("LIGHT", theme.LIGHT), ("DARK", theme.DARK)):
        for k, v in palette.items():
            assert _is_hex(v), f"{name}.{k} = {v!r} 不是合法 hex 颜色"
    assert _is_hex(theme.ACCENT)
    for c in theme.PALETTE:
        assert _is_hex(c), f"PALETTE 含非法 hex: {c!r}"


def test_theme_palette_length():
    assert len(theme.PALETTE) == 10


def test_theme_sizes():
    assert theme.ICON_SIZE == 18
    assert theme.TB_RADIUS == 8
    assert theme.BTN_RADIUS == 6
    assert theme.BTN_PAD_X == 8
    assert theme.BTN_PAD_Y == 6
    assert theme.SEP_W == 1
    assert theme.SEP_H == 18
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_theme.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'theme'"

- [ ] **Step 3: Implement `theme.py`**

```python
# theme.py
"""三处工具条共享的设计 token（颜色、字体、间距、圆角）。"""
import tkinter as tk
from PIL import Image, ImageTk


# 字体
FONT = ("Microsoft YaHei UI", 9)
FONT_ICON = ("Microsoft YaHei UI", 10)   # 菜单项等带文字处

# 尺寸
ICON_SIZE = 18          # 图标渲染像素
BTN_PAD_X = 8
BTN_PAD_Y = 6
TB_RADIUS = 8           # 工具条容器圆角
BTN_RADIUS = 6          # 按钮圆角（本期仅 token）
SEP_W, SEP_H = 1, 18    # 分隔线

# —— 浅色工具条（选区，仿飞书）——
LIGHT = {
    "surface":  "#ffffff",
    "border":   "#e5e6eb",
    "fg":       "#1f2329",
    "hover":    "#f2f3f5",
    "hover_fg": "#1f2329",
    "active":   "#4a90e2",
    "active_fg": "#ffffff",
    "disabled": "#c9cdd4",
    "sep":      "#e5e6eb",
}

# —— 深色工具条（编辑器 / 托盘菜单）——
DARK = {
    "surface":  "#2b2f36",
    "border":   "#4a5160",
    "fg":       "#f5f6f8",
    "hover":    "#4a90e2",
    "hover_fg": "#ffffff",
    "btn":      "#3a3f47",
    "btn_fg":   "#f5f6f8",
    "btn_hover": "#4a5160",
    "active":   "#4a90e2",
    "active_fg": "#ffffff",
    "primary":  "#4a90e2",
    "primary_hover": "#3d7dc9",
    "sep":      "#4a5160",
    "confirm":  "#22c55e",
    "cancel":   "#f5484d",
}

# 强调色（图标默认色 / 选区 active 等共用）
ACCENT = "#4a90e2"
# 预设色板（选区工具条颜色行用；第一色 = 默认画图色）
PALETTE = ["#f5484d", "#3370ff", "#86909c",
           "#4e5969", "#ffab00", "#22c55e",
           "#3490ff", "#1f2329", "#a0a6b0", "#ffffff"]


def _hex_to_rgb(color: str) -> tuple:
    """#rgb 或 #rrggbb → (r, g, b)。"""
    v = color.lstrip("#")
    if len(v) == 3:
        v = "".join(ch * 2 for ch in v)
    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)


def rounded_rect_photo(master, w, h, radius, fill, outline) -> "tk.PhotoImage":
    """生成 w×h 的圆角矩形 PhotoImage（fill 底 + outline 1px 边框）。

    用于工具条/菜单容器背景（Canvas 铺底，规避 tkinter 直角限制）。
    返回的 PhotoImage 由 master 持有，生命周期随 master。
    """
    if w < 2 or h < 2:
        # 退化：太小的窗口不画圆角（避免 PIL 报错）
        img = Image.new("RGBA", (max(1, w), max(1, h)),
                        (*_hex_to_rgb(fill), 255))
        return ImageTk.PhotoImage(img, master=master)
    r = max(0, min(radius, w // 2, h // 2))
    f, o = _hex_to_rgb(fill), _hex_to_rgb(outline)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    # 用 ImageDraw 画圆角矩形（PIL 8.2+ 支持 radius 参数）
    from PIL import ImageDraw
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, w - 1, h - 1), radius=r, fill=(*f, 255),
                        outline=(*o, 255) if outline else None, width=1)
    return ImageTk.PhotoImage(img, master=master)
```

注意：`ImageTk.PhotoImage(img, master=master)` 的 `master` 参数让 PhotoImage 绑定到该 Tk 解释器；`rounded_rect_photo` 在测试中不调用（避免真实 Tk），只测常量与 `rounded_rect_photo` 的签名。若测试环境无显示，`ImageTk.PhotoImage` 会抛 `TclError`——`rounded_rect_photo` 不在测试路径上，无影响。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_theme.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add theme.py tests/test_theme.py
git commit -m "feat: add theme.py design tokens (LIGHT/DARK/PALETTE/rounded_rect_photo)"
```

---

### Task 2: `assets/icons/*.svg` 16 个线性图标

**Files:**
- Create: `assets/icons/brush.svg`, `rect.svg`, `ellipse.svg`, `arrow.svg`, `mosaic.svg`, `text.svg`, `crop.svg`, `palette.svg`, `undo.svg`, `copy.svg`, `save.svg`, `ocr.svg`, `confirm.svg`, `cancel.svg`, `region.svg`, `fullscreen.svg`
- Create: `assets/icons/README.md`（来源与许可说明）

**Interfaces:**
- Consumes: 无。
- Produces: 16 个 SVG 文件，每个 `viewBox="0 0 20 20"`、`fill="none"`、`stroke="#4a90e2"`（占位色，运行时由 `icon.render` 重着色）、`stroke-width="2"`、`stroke-linecap="round"`、`stroke-linejoin="round"`，留 2px 安全边距（图形在 [2,2]~[18,18] 内）。

- [ ] **Step 1: 创建目录与 16 个 SVG 文件**

每个 SVG 是单行 XML（无换行，方便 PyInstaller 打包）。模板（以 `ellipse.svg` 为例）：

```xml
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="none" stroke="#4a90e2" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="10" cy="10" r="7"/></svg>
```

16 个图标的路径（Lucide 风格，ISC 许可，改绘为 20×20）：

| 文件 | 用途 | SVG 路径内容（`<svg>` 标签内的几何） |
|---|---|---|
| `brush.svg` | 画笔 | `<path d="M14 3 L17 6 L8 15 L4 16 L5 12 Z"/><path d="M4 16 L3 17"/>` |
| `rect.svg` | 矩形 | `<rect x="3" y="4" width="14" height="12" rx="1"/>` |
| `ellipse.svg` | 圆形 | `<circle cx="10" cy="10" r="7"/>` |
| `arrow.svg` | 箭头 | `<path d="M4 16 L16 4"/><path d="M8 4 L16 4 L16 12"/>` |
| `mosaic.svg` | 打码 | `<rect x="3" y="3" width="14" height="14" rx="1"/><path d="M10 3 V17 M3 10 H17"/>` |
| `text.svg` | 文字 | `<path d="M5 5 H15 M10 5 V16"/>` |
| `crop.svg` | 裁剪 | `<path d="M6 3 V13 H16"/><path d="M3 6 H13 V16"/>` |
| `palette.svg` | 取色 | `<path d="M10 3 A7 7 0 1 0 17 10 H10 Z"/><circle cx="7" cy="8" r="1"/><circle cx="10" cy="6" r="1"/><circle cx="13" cy="8" r="1"/>` |
| `undo.svg` | 撤销 | `<path d="M5 8 H13 A4 4 0 1 1 13 14 H8"/><path d="M8 5 L5 8 L8 11"/>` |
| `copy.svg` | 复制 | `<rect x="7" y="7" width="10" height="10" rx="1"/><path d="M4 13 V4 A1 1 0 0 1 5 3 H14"/>` |
| `save.svg` | 保存 | `<path d="M4 4 H14 L16 6 V16 H4 Z"/><rect x="6" y="12" width="8" height="4"/><path d="M6 4 V8 H12 V4"/>` |
| `ocr.svg` | 识别 | `<path d="M3 7 V4 A1 1 0 0 1 4 3 H7 M13 3 H16 A1 1 0 0 1 17 4 V7 M17 13 V16 A1 1 0 0 1 16 17 H13 M7 17 H4 A1 1 0 0 1 3 16 V13"/><path d="M7 10 H13"/>` |
| `confirm.svg` | 确认 | `<path d="M4 10 L8 14 L16 6"/>` |
| `cancel.svg` | 取消/关闭/退出 | `<path d="M5 5 L15 15 M15 5 L5 15"/>` |
| `region.svg` | 框选截图 | `<rect x="3" y="3" width="14" height="14" rx="1" stroke-dasharray="3 2"/>` |
| `fullscreen.svg` | 全屏截图 | `<rect x="3" y="4" width="14" height="10" rx="1"/><path d="M7 17 H13"/>` |

> 实现时按上表逐个写文件。每个 SVG 单行，无缩进。`README.md` 写：图标源自 Lucide（ISC 许可，lucide.dev），改绘为 20×20、stroke-width=2、round 端点，运行时由 `icon.py` 重着色。

- [ ] **Step 2: 验证 SVG 文件可被 svglib 解析**

Run:
```bash
python -c "
import os, io
from svglib.svglib import svg2rlg
d = os.path.join('assets', 'icons')
for f in sorted(os.listdir(d)):
    if not f.endswith('.svg'): continue
    with open(os.path.join(d, f), 'rb') as fh:
        drawing = svg2rlg(io.BytesIO(fh.read()))
    print(f, drawing.width, drawing.height)
"
```
Expected: 16 行输出，每行 `<name>.svg 20.0 20.0`。若某文件解析失败，修正该 SVG 后重跑。

- [ ] **Step 3: Commit**

```bash
git add assets/icons/
git commit -m "feat: add 16 SVG icons (Lucide style, 20x20, stroke=2)"
```

---

### Task 3: `icon.py` SVG 渲染器 + 测试

**Files:**
- Create: `icon.py`
- Test: `tests/test_icon.py`

**Interfaces:**
- Consumes: `theme.ICON_SIZE`（默认尺寸）。
- Produces: `icon.render(name, color, size=theme.ICON_SIZE) -> tk.PhotoImage`（缓存命中零渲染；renderer 不可用时返回 1×1 透明图）, `icon._RENDERER_AVAILABLE: bool`, `icon._reset_cache() -> None`（测试用）。

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_icon.py
import os
from unittest.mock import patch, MagicMock

import pytest

import icon
import theme

ASSETS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "icons")


@pytest.fixture(autouse=True)
def _reset():
    icon._reset_cache()
    yield
    icon._reset_cache()


def test_render_returns_photoimage_of_size():
    photo = icon.render("ellipse", "#4a90e2", size=18)
    assert photo is not None
    # PhotoImage 的 width()/height() 需要真实 Tk；用 mock master 时
    # 这里只断言对象存在 + 缓存命中
    assert photo is icon.render("ellipse", "#4a90e2", size=18)


def test_render_different_colors_differ():
    a = icon.render("ellipse", "#4a90e2", size=18)
    b = icon.render("ellipse", "#ff0000", size=18)
    assert a is not b  # 不同 color → 不同缓存条目


def test_render_missing_svg_returns_blank():
    photo = icon.render("does_not_exist_xyz", "#4a90e2", size=18)
    assert photo is not None  # 不抛异常


def test_render_missing_renderer_no_crash():
    """renderer 不可用（svglib/reportlab 未装）时返回 1×1 透明图，不抛异常。"""
    with patch.dict("sys.modules", {"svglib": None, "reportlab": None}):
        icon._reset_cache()
        photo = icon.render("ellipse", "#4a90e2", size=18)
    assert photo is not None
    assert icon._RENDERER_AVAILABLE is False


def test_render_cache_hit_same_object():
    a = icon.render("ellipse", "#4a90e2", size=18)
    b = icon.render("ellipse", "#4a90e2", size=18)
    assert a is b
```

> 测试不要求真实渲染像素（无显示环境），只验证：返回非 None、缓存命中、不同 color 不同对象、缺失 svg/缺失 renderer 不崩。`test_render_missing_renderer_no_crash` 用 `patch.dict` 把 `svglib`/`reportlab` 设为 None，让 `import svglib` 失败。

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_icon.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'icon'"

- [ ] **Step 3: Implement `icon.py`**

```python
# icon.py
"""SVG 图标 → ImageTk.PhotoImage 渲染（支持重着色 + 缩放 + 缓存）。

渲染后端：svglib + reportlab + rlPyCairo（纯 Python，无需系统 cairo 库）。
懒加载：顶层不 import svglib/reportlab，首次 render() 时才 import。
renderer 不可用时返回 1×1 透明 PhotoImage（不抛异常），保证 UI 不崩。
"""
import io
import os
import tkinter as tk
from PIL import Image, ImageTk

from theme import ICON_SIZE

_ROOT = os.path.dirname(os.path.abspath(__file__))
_ICONS_DIR = os.path.join(_ROOT, "assets", "icons")

_cache = {}                  # (name, color, size) -> PhotoImage
_RENDERER_AVAILABLE = True   # 首次 render 时探测
_renderer_failed = False     # 已探测失败，不再重试


def _icon_path(name: str) -> str:
    return os.path.join(_ICONS_DIR, f"{name}.svg")


def _hex_to_rgb(color: str) -> tuple:
    v = color.lstrip("#")
    if len(v) == 3:
        v = "".join(ch * 2 for ch in v)
    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)


def _svg_to_rgba(svg_path: str, color: str, size: int) -> Image.Image:
    """svglib 渲染 SVG → ARGB32 → PIL RGBA，按 color 重着色（保留 alpha）。"""
    from svglib.svglib import svg2rlg
    from reportlab.graphics import renderPM

    with open(svg_path, "rb") as fh:
        svg_bytes = fh.read()
    drawing = svg2rlg(io.BytesIO(svg_bytes))
    drawing.width = size
    drawing.height = size
    canvas = renderPM.drawToPMCanvas(drawing, dpi=72, bg=0x00000000,
                                     backendFmt="RGBA")
    w, h = canvas._gs.width, canvas._gs.height
    pix = canvas._gs.pixBuf
    img = Image.new("RGBA", (w, h))
    img.frombytes(pix, "raw", "ARGB")
    if img.size != (size, size):
        img = img.resize((size, size), Image.LANCZOS)
    # 重着色：保留 alpha，RGB 替换为 color
    c = _hex_to_rgb(color)
    out = Image.new("RGBA", img.size, (*c, 255))
    out.putalpha(img.getchannel("A"))
    return out


def _blank_photo(master, size: int) -> "tk.PhotoImage":
    """1×1 透明占位图（renderer 不可用或 svg 缺失时）。"""
    img = Image.new("RGBA", (max(1, size), max(1, size)), (0, 0, 0, 0))
    return ImageTk.PhotoImage(img, master=master)


def render(name: str, color: str, size: int = ICON_SIZE,
           master=None) -> "tk.PhotoImage":
    """渲染 assets/icons/<name>.svg 为 PhotoImage，按 color 着色，size×size。

    - 同一 (name, color, size) 只渲染一次，后续命中缓存。
    - 图标文件不存在 / renderer 不可用：返回 size×size 透明 PhotoImage（不抛异常）。
    - master: 可选的 Tk 根/窗口，PhotoImage 绑定到它（避免被 GC）。
    """
    global _RENDERER_AVAILABLE, _renderer_failed
    key = (name, color, size)
    if key in _cache:
        return _cache[key]
    photo = None
    svg_path = _icon_path(name)
    if os.path.exists(svg_path) and not _renderer_failed:
        try:
            img = _svg_to_rgba(svg_path, color, size)
            photo = ImageTk.PhotoImage(img, master=master)
        except Exception:
            # renderer 不可用（import 失败 / 渲染异常）：标记失败，后续快速退化
            _renderer_failed = True
            _RENDERER_AVAILABLE = False
    if photo is None:
        photo = _blank_photo(master, size)
    _cache[key] = photo
    return photo


def _reset_cache():
    """清空缓存（测试用）。"""
    global _renderer_failed, _RENDERER_AVAILABLE
    _cache.clear()
    _renderer_failed = False
    _RENDERER_AVAILABLE = True
```

> 关键点：
> - `render()` 的 `master` 参数默认 None，但 `ImageTk.PhotoImage(img, master=None)` 在真实 Tk 环境下可用（绑定到默认根）。测试环境无真实 Tk 时，`ImageTk.PhotoImage` 会抛 `TclError`——但测试里 `render()` 的 svg 路径不存在（`tests/` 下无 `assets/icons`）或 renderer 被 mock 掉，走 `_blank_photo` 分支；`_blank_photo` 也调 `ImageTk.PhotoImage`，同样会抛。**因此 `test_icon.py` 需要 mock `ImageTk.PhotoImage`**——在 fixture 里加 `m.setattr(icon.ImageTk, "PhotoImage", MagicMock())`。
> - 修正：在 `test_icon.py` 的 `_reset` fixture 里加 `with patch.object(icon.ImageTk, "PhotoImage") as m_pi: m_pi.return_value = MagicMock(); yield`。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_icon.py -v`
Expected: PASS (5 tests)。若 `ImageTk.PhotoImage` 在无 Tk 环境抛 `TclError`，按 Step 3 末尾的修正加 mock。

- [ ] **Step 5: Commit**

```bash
git add icon.py tests/test_icon.py
git commit -m "feat: add icon.py SVG renderer (svglib+reportlab, lazy, cached)"
```

---

### Task 4: `selector.py` 改造（浅色工具条）

**Files:**
- Modify: `selector.py`
- Test: `tests/test_selector.py`（适配：按钮改图片后，`test_toolbar_disabled_in_select_mode` / `test_toolbar_enabled_in_adjust_mode` 断言 `fg=` 改为断言 `image=`）

**Interfaces:**
- Consumes: `theme.LIGHT`（浅色 token）, `theme.PALETTE`（色板，从 selector 移入 theme）, `theme.ICON_SIZE`, `theme.FONT`, `theme.FONT_ICON`, `theme.BTN_PAD_X/Y`, `theme.TB_RADIUS`, `theme.SEP_W/H`, `theme.rounded_rect_photo`, `icon.render`。
- Produces: 无新接口；`Selector` 的公共行为不变（`_select_tool` / `_confirm` / `_cancel` / `_copy` / `_save` / `_ocr` / `_undo` 签名不变）。

**改造要点（不改变交互逻辑）：**

1. 顶部 `from theme import LIGHT, PALETTE, FONT, FONT_ICON, ICON_SIZE, BTN_PAD_X, BTN_PAD_Y, TB_RADIUS, SEP_W, SEP_H, rounded_rect_photo` + `import icon`。
2. 删除本地 `TB_BG` / `TB_FG` / `TB_HOVER` / `TB_SEP` / `TB_DISABLED` / `TB_ACTIVE` / `TB_FONT` / `TB_ICON_FONT` / `TB_CONFIRM_FG` / `TB_CANCEL_FG` / `PALETTE` 常量（改引用 theme）。
3. `TOOL_ICONS` 字典保留（作为 icon name 映射），值改为 SVG 文件名（`"brush" → "brush"` 等，与 assets/icons 同名）。
4. `_build_toolbar`：
   - 容器圆角：`tb_win` 内放 `tk.Canvas`（`highlightthickness=0`，铺满），Canvas 上 `create_image` 一张 `rounded_rect_photo(tb_win, w, h, TB_RADIUS, LIGHT["surface"], LIGHT["border"])`，按钮用 Canvas 的 `create_window` 摆放。`tb_win` 的 `<Configure>` 触发重算圆角图尺寸。
   - 工具按钮：`tk.Label(image=icon.render(name, LIGHT["fg"]))`，`padx=BTN_PAD_X, pady=BTN_PAD_Y`。hover 切 `image=icon.render(name, LIGHT["hover_fg"])`（底 `LIGHT["hover"]`）；选中切 `image=icon.render(name, LIGHT["active_fg"])`（底 `LIGHT["active"]`）。
   - 操作按钮（`_tb_btn`）：`tk.Label(image=icon.render(name, fg))`，确认 `fg=LIGHT["active"]` 对应 confirm 绿（`theme.DARK["confirm"]`），取消 `fg=theme.DARK["cancel"]`。
   - 颜色按钮：`icon.render("palette", LIGHT["fg"])`。
   - 粗细 Spinbox：保留，配色改 `bg=LIGHT["surface"], fg=LIGHT["fg"], highlightbackground=LIGHT["sep"]`。
   - 分隔线：`tk.Frame(bar, width=SEP_W, height=SEP_H, bg=LIGHT["sep"])`。
5. `_tb_hover` / `_tb_btn` / `_set_toolbar_enabled`：改为切换 `configure(image=...)`，不再切 `configure(fg=...)` / `configure(text=...)`。
6. 禁用态（select 模式）：图标渲染 `LIGHT["disabled"]` 灰。
7. 尺寸标签 / 十字准星 / 遮罩：不动（`LABEL_BG` / `LABEL_FG` / `CROSSHAIR_COLOR` / `MASK_ALPHA` 保留）。

**测试适配：**
- `test_toolbar_disabled_in_select_mode`：原断言 `c.kwargs.get("fg") == TB_DISABLED`，改为断言 `c.kwargs.get("image")` 是 `icon.render(..., LIGHT["disabled"], ...)` 的返回（或断言 `configure` 被调用且 kwargs 含 `image`）。
- `test_toolbar_enabled_in_adjust_mode`：同理，断言 `image` 被设置为 `LIGHT["fg"]` 色图标。
- 由于 `icon.render` 在无 Tk 环境返回 mock 的 `PhotoImage`（Task 3 的 mock），`configure(image=...)` 的 kwargs 里 `image` 是 mock 对象，测试可断言 `c.kwargs.get("image") is not None`。

- [ ] **Step 1: 适配现有测试（改断言 fg → image）**

修改 `tests/test_selector.py`：
- `test_toolbar_disabled_in_select_mode`：删除 `from selector import TB_DISABLED`，改为 `from theme import LIGHT`，断言 `[c for c in b.configure.call_args_list if c.kwargs.get("image") is not None]` 非空。
- `test_toolbar_enabled_in_adjust_mode`：删除 `from selector import TB_FG`，改为 `from theme import LIGHT`，断言 `[c for c in b.configure.call_args_list if c.kwargs.get("image") is not None]` 非空。
- `fake_root` fixture 加 `m.setattr("icon.ImageTk.PhotoImage", MagicMock())`（若 Task 3 的 mock 不覆盖 selector 的 import）。

- [ ] **Step 2: Run existing tests to see which fail**

Run: `pytest tests/test_selector.py -v`
Expected: 部分 FAIL（按钮改图片后，`configure(image=...)` 的断言与原 `fg=` 断言不匹配）。

- [ ] **Step 3: 改造 `selector.py`**

按上面"改造要点"逐条改。关键函数签名不变：
- `_build_toolbar(self)`：内部改用 `icon.render` + `rounded_rect_photo`。
- `_tb_hover(self, b, enter)`：改 `b.configure(image=...)`。
- `_tb_btn(self, parent, key, name, command, fg=None)`：参数从 `text` 改 `name`（icon 名），内部 `tk.Label(parent, image=icon.render(name, fg))`。
- `_set_toolbar_enabled(self, enabled)`：改 `b.configure(image=icon.render(..., disabled/fg/active))`。
- `_select_tool(self, name)`：改 `b.configure(image=icon.render(..., active/fg))`。

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_selector.py -v`
Expected: PASS（所有原有测试 + 适配后的断言）。

- [ ] **Step 5: Commit**

```bash
git add selector.py tests/test_selector.py
git commit -m "feat: selector toolbar uses SVG icons + theme tokens (light)"
```

---

### Task 5: `editor.py` 改造（深色工具栏）

**Files:**
- Modify: `editor.py`
- Test: `tests/test_editor.py`（现有测试只测 `UndoStack` / `mosaic`，不测工具条；无需改）

**Interfaces:**
- Consumes: `theme.DARK`（深色 token）, `theme.FONT`, `theme.ICON_SIZE`, `theme.BTN_PAD_X/Y`, `theme.TB_RADIUS`, `theme.SEP_W/H`, `theme.rounded_rect_photo`, `icon.render`。
- Produces: 无新接口；`Editor` 的公共行为不变。

**改造要点：**

1. 顶部 `from theme import DARK, FONT, ICON_SIZE, BTN_PAD_X, BTN_PAD_Y, TB_RADIUS, SEP_W, SEP_H, rounded_rect_photo` + `import icon`。
2. 删除 `_build_toolbar` 里的局部 `TB_BG` / `TB_FG` / `TB_HOVER` / `TB_HOVER_FG` / `TB_BTN_BG` / `TB_BTN_BORDER` / `FONT` 常量（改引用 `DARK`）。
3. 删除类属性 `BG` / `BG_DARK` / `FG` / `FG_SELECTED` / `BTN_BG` / `BTN_BORDER` / `BTN_HOVER` / `ACCENT` / `SEPARATOR`（保留 `CANVAS_BG_DEFAULT`）。
4. `_build_toolbar`：
   - 容器圆角：`toolbar` 内 Canvas + `rounded_rect_photo(toolbar, w, h, TB_RADIUS, DARK["surface"], DARK["border"])`，`<Configure>` 重算。
   - 工具组 7 按钮（brush/rect/ellipse/arrow/mosaic/text/crop）：`tk.Label(image=icon.render(name, DARK["fg"]))`，底 `DARK["btn"]`。hover 切 `image=icon.render(name, DARK["hover_fg"])`（底 `DARK["hover"]`）；选中切 `image=icon.render(name, DARK["active_fg"])`（底 `DARK["active"]`）。
   - 颜色/背景色按钮：保留色块（`tk.Button`，非图标）。
   - 粗细 Spinbox：保留，配色改 `bg=DARK["btn"], fg=DARK["fg"], highlightbackground=DARK["sep"]`。
   - 操作按钮 `_tb_action_btn`：改 `tk.Label(image=icon.render(name, fg), text=label)` 不行——tkinter Label 不能同时 image + text。改为 Frame 内放 `tk.Label(image=...)` + `tk.Label(text=label)`，间距 4。primary（保存）底 `DARK["primary"]`，hover `DARK["primary_hover"]`，白图标白字。
   - 分隔线：`tk.Frame(bar, width=SEP_W, height=SEP_H, bg=DARK["sep"])`。
5. `_restool_hover_tb` / `_apply_tool_style`：改 `btn.configure(image=...)`，不再切 `configure(fg=...)`。
6. 跟随/拖动/最小宽度逻辑：不动（`_tb_start_follow` / `_tb_do_follow` / `_tb_sync_with_window` / `_position_toolbar` 等保持）。

- [ ] **Step 1: Run existing tests to verify baseline**

Run: `pytest tests/test_editor.py -v`
Expected: PASS (3 tests: undo_stack_basic, undo_stack_maxlen, mosaic_reduces_detail)。

- [ ] **Step 2: 改造 `editor.py`**

按上面"改造要点"逐条改。关键函数：
- `_build_toolbar(self)`：容器改 Canvas + 圆角 PhotoImage；工具按钮改 `icon.render`；操作按钮改 Frame + 图标 Label + 文字 Label。
- `_tb_action_btn(self, parent, name, label, command, primary=False)`：参数从 `icon`（字符）改 `name`（icon 名）；内部 Frame 布局。
- `_restool_hover_tb(self, name, btn)`：改 `btn.configure(image=icon.render(name, active_fg/fg))`。
- `_apply_tool_style(self, name, btn)`：改 `btn.configure(image=...)`。
- `_mk_action_btn`（如保留）：改 token 颜色。

- [ ] **Step 3: Run tests to verify they pass**

Run: `pytest tests/test_editor.py -v`
Expected: PASS（3 tests 不变，工具条改造不影响这些测试）。

- [ ] **Step 4: Commit**

```bash
git add editor.py
git commit -m "feat: editor toolbar uses SVG icons + theme tokens (dark)"
```

---

### Task 6: `app.py` 改造（托盘悬浮菜单，深色）

**Files:**
- Modify: `app.py`
- Test: `tests/test_app.py`（现有测试只测 `process_fullscreen_capture`，不测 FloatingMenu；无需改）

**Interfaces:**
- Consumes: `theme.DARK`, `theme.FONT_ICON`, `theme.ICON_SIZE`, `theme.BTN_PAD_X/Y`, `theme.TB_RADIUS`, `theme.rounded_rect_photo`, `icon.render`。
- Produces: 无新接口；`FloatingMenu` 的公共行为不变（`show_at` / `close`）。

**改造要点：**

1. 顶部 `from theme import DARK, FONT_ICON, ICON_SIZE, BTN_PAD_X, BTN_PAD_Y, TB_RADIUS, rounded_rect_photo` + `import icon`。
2. 删除 `FloatingMenu` 类属性 `BG` / `FG` / `HOVER` / `HOVER_FG` / `SEP` / `FONT`（保留 `PAD_X` / `PAD_Y` / `MIN_W` 或改引用 theme）。
3. `_get_menu` 里的 items：label 从 emoji 文字（"📷 框选截图"）改为 `(label, command, icon_name)` 三元组，icon_name 为 `region` / `fullscreen` / `copy` / `cancel`。
4. `FloatingMenu.__init__(items, root)`：items 变为 `[(label, command, icon_name), ...]`，分隔线仍用 `label is None`。
5. `show_at`：
   - 容器圆角：`win` 内 Canvas + `rounded_rect_photo(win, w, h, TB_RADIUS, DARK["surface"], DARK["border"])`。
   - 菜单项：`tk.Frame` 内放 `tk.Label(image=icon.render(icon_name, DARK["fg"]))` + `tk.Label(text=label, fg=DARK["fg"], font=FONT_ICON)`，间距 4。hover 切 `image=icon.render(icon_name, DARK["hover_fg"])` + 底 `DARK["hover"]`。
   - 分隔线：`tk.Frame(inner, height=1, bg=DARK["sep"])`。
6. `main` 里的 `_get_menu`：items 改为三元组：
   ```python
   items = [
       ("框选截图", do_region_capture, "region"),
       ("全屏截图", do_fullscreen_capture, "fullscreen"),
       ("复制截图", do_copy_capture, "copy"),
       (None, None, None),  # 分隔线
       ("退出", on_quit, "cancel"),
   ]
   ```

- [ ] **Step 1: Run existing tests to verify baseline**

Run: `pytest tests/test_app.py -v`
Expected: PASS (1 test: test_process_fullscreen_saves_image)。

- [ ] **Step 2: 改造 `app.py`**

按上面"改造要点"逐条改。关键：
- `FloatingMenu.__init__` / `show_at`：items 三元组 + 图标 Label + 圆角容器。
- `_get_menu`：items 改三元组。
- 删除类属性颜色常量，改引用 `DARK`。

- [ ] **Step 3: Run tests to verify they pass**

Run: `pytest tests/test_app.py -v`
Expected: PASS（1 test 不变）。

- [ ] **Step 4: Commit**

```bash
git add app.py
git commit -m "feat: floating menu uses SVG icons + theme tokens (dark)"
```

---

### Task 7: PyInstaller spec + requirements.txt

**Files:**
- Modify: `screen-shot.spec`
- Modify: `requirements.txt`

**Interfaces:**
- Consumes: `assets/icons/`（16 个 SVG）, `svglib` / `reportlab` / `rlPyCairo` 包。
- Produces: 打包后 exe 含 `assets/icons` 与渲染器数据。

- [ ] **Step 1: 修改 `requirements.txt`**

追加三行：
```
svglib
reportlab
rlPyCairo
```

- [ ] **Step 2: 修改 `screen-shot.spec`**

在 `datas` 里加 `assets/icons`：
```python
_root = os.path.dirname(os.path.abspath(__file__))
_icon_datas = [(os.path.join(_root, 'assets', 'icons'), 'assets/icons')]
_datas = _ocr_datas + _icon_datas
```
`hiddenimports` 加 `'svglib', 'reportlab', 'rlPyCairo', 'pycairo'`。

- [ ] **Step 3: 验证 spec 语法**

Run: `python -c "exec(open('screen-shot.spec').read())" 2>&1 | head -5`
Expected: 可能报 `NameError: Analysis is not defined`（spec 依赖 PyInstaller 的 `Analysis` 全局名，直接 exec 会失败）——这是预期的，只验证无 `SyntaxError`。

- [ ] **Step 4: Commit**

```bash
git add screen-shot.spec requirements.txt
git commit -m "build: package assets/icons + svglib/reportlab/rlPyCairo in spec"
```

---

### Task 8: 全量测试 + 手动高 DPI 验证

**Files:**
- Modify: 无（只跑测试 + 手动验证）。

- [ ] **Step 1: 跑全量测试**

Run: `pytest tests/ -v`
Expected: 全部 PASS。若有失败，回到对应 Task 修复。

- [ ] **Step 2: 手动验证（可选，需真实 GUI）**

Run: `python tests/manual_selector.py`
Expected: 弹选区窗口，工具条图标为矢量（高 DPI 150% 下清晰），hover/选中/禁用态图标变色正常，圆角容器观感 OK。

Run: `python -c "from PIL import Image; from editor import Editor; Editor(Image.new('RGB',(400,300),'white'), on_close=None).show()"`
Expected: 编辑器窗口，工具栏图标为矢量，深色背景，圆角容器观感 OK。

- [ ] **Step 3: Commit（如有修复）**

```bash
git add -A
git commit -m "fix: address issues found in full test run"
```

---

## Self-Review

1. **Spec coverage**：
   - `theme.py` → Task 1 ✓
   - `assets/icons/*.svg`（16 个）→ Task 2 ✓
   - `icon.py` + `test_icon.py` → Task 3 ✓
   - `selector.py` 改造（浅色）→ Task 4 ✓
   - `editor.py` 改造（深色）→ Task 5 ✓
   - `app.py` 改造（深色菜单）→ Task 6 ✓
   - `screen-shot.spec` + `requirements.txt` → Task 7 ✓
   - 全量测试 + 手动高 DPI 验证 → Task 8 ✓
   - 状态样式矩阵（默认/hover/选中/primary/确认/取消/禁用）→ Task 4-6 的 hover/选中/禁用逻辑 ✓
   - 圆角只容器 → Task 1 的 `rounded_rect_photo` + Task 4-6 的 Canvas 容器 ✓

2. **Step scan**：每个 step 都是单一动作（写测试/跑测试/实现/验证/commit）。Task 3 的 Step 3 末尾有"修正"说明（mock `ImageTk.PhotoImage`），已并入 step 不另起 step。

3. **Type consistency**：
   - `icon.render(name, color, size=theme.ICON_SIZE, master=None)` 在 Task 3 定义，Task 4-6 调用时传 `master=tb_win/toolbar/win` ✓
   - `theme.rounded_rect_photo(master, w, h, radius, fill, outline)` 在 Task 1 定义，Task 4-6 调用 ✓
   - `theme.LIGHT` / `theme.DARK` 的键在 Task 1 的测试里断言，Task 4-6 引用 ✓

4. **Review Focus**：
   - 高 DPI 清晰度 → Task 8 手动验证 ✓
   - renderer 缺失不崩 → Task 3 `test_render_missing_renderer_no_crash` ✓
   - 缓存命中零渲染 → Task 3 `test_render_cache_hit_same_object` ✓
   - 圆角 resize 重算 → Task 4-6 的 `<Configure>` 绑定 + Task 8 手动验证 ✓
   - 颜色 token 完整 → Task 1 `test_theme_light_keys` / `test_theme_dark_keys` / `test_theme_hex_colors` ✓

5. **Proportion**：plan 长度与 spec 相当（spec 263 行，plan ~600 行含代码块）。代码块主要是 Task 1 的 `theme.py`（完整实现，因为 token 值从 spec 复制）和 Task 3 的 `icon.py`（渲染算法 signature + 测试不确定的部分）。Task 2 的 SVG 路径表是"exact copy"（spec 没给路径，需逐个手绘）。Task 4-6 只有"改造要点"（签名 + 要点，不写完整代码）。
