### Task 3: `icon.py` SVG 渲染器 + 测试

**Files:**
- Create: `icon.py`
- Test: `tests/test_icon.py`

**Interfaces:**
- Consumes: `theme.ICON_SIZE`（默认尺寸）。
- Produces: `icon.render(name, color, size=theme.ICON_SIZE, master=None) -> tk.PhotoImage`（缓存命中零渲染；renderer 不可用时返回 1×1 透明图）, `icon._RENDERER_AVAILABLE: bool`, `icon._reset_cache() -> None`（测试用）。

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
