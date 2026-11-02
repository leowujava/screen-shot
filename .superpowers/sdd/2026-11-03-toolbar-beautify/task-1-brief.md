### Task 1: `theme.py` 设计 token + 测试

**Files:**
- Create: `theme.py`
- Test: `tests/test_theme.py`

**Interfaces:**
- Consumes: 无（叶子模块）。
- Produces: `theme.FONT`, `theme.FONT_ICON`, `theme.ICON_SIZE`(18), `theme.BTN_PAD_X`(8), `theme.BTN_PAD_Y`(6), `theme.TB_RADIUS`(8), `theme.BTN_RADIUS`(6), `theme.SEP_W`(1), `theme.SEP_H`(18), `theme.LIGHT`（dict, 9 键）, `theme.DARK`（dict, 15 键）, `theme.ACCENT`("#4a90e2"), `theme.PALETTE`（list, 10 色）, `theme.rounded_rect_photo(master, w, h, radius, fill, outline)` → `PIL.ImageTk.PhotoImage`。

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
