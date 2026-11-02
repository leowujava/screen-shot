# 工具栏整体美化（SVG 图标 + 统一 token）—— 设计

## 意图与背景

三处 UI 工具（选区工具条 `selector.py`、编辑工具栏 `editor.py`、托盘悬浮菜单 `app.py`）目前：

- 图标用 Unicode 字符（✏ ⬜ ◯ ↗ ▒ T 🎨 ↩ ⧉ 🔍 💾 ✓ ✕ 等），高 DPI 下放大发虚，且无法按状态变色，观感糙。
- 颜色/字体/间距硬编码散落在三个文件，同一 token（如 `#4a90e2`、`#2b2f36`）重复定义 5+ 处，改一处漏多处。

目标：换成真正的矢量 SVG 图标（可随状态变色），抽取统一设计 token 文件，规范间距与按钮状态。

## 目标

1. 新增 `theme.py`：三处工具条共享的设计 token（颜色、字体、间距、圆角）。
2. 新增 `assets/icons/*.svg`：一套线性风格图标，覆盖全部工具 + 操作按钮 + 菜单项。
3. 新增 `icon.py`：SVG → `ImageTk` 渲染器，支持按颜色重着色 + 尺寸缩放 + 缓存。
4. 三处工具条改用图片按钮（`tk.Label(image=...)`），保留现有交互（hover/选中/禁用/跟随）。
5. 统一间距与状态样式；按钮支持圆角（用预渲染圆角图片做按钮背景，规避 tkinter 直角限制）。
6. PyInstaller spec 显式打包 `assets/icons`。

## 非目标（本次不做）

- 不改变任何交互逻辑：工具切换、颜色行、粗细、撤销、OCR、复制/保存/确认/取消、工具条跟随/拖动行为全部保持。
- 不改变功能：不新增/删除按钮，不改快捷键。
- 不改托盘图标（pystray 的 `_make_tray_icon_image`，本次只做悬浮菜单）。
- 不改画布/选区本身（遮罩、手柄、十字准星）。
- 不做图标动画（hover 渐变等 tkinter 做不到的）。

## 约束

- Python + tkinter，Windows 优先；共享 Tk 根窗口架构不变。
- 新增依赖仅 `cairosvg`（SVG→PNG 渲染）。PyInstaller onefile 需显式打包 assets 与 cairosvg 数据。
- 渲染开销：图标在启动/首次使用时渲染一次并缓存，运行时零开销。
- 图片按钮需保持可点击（`tk.Label` + 绑定，与现状一致）。

## 方案（已选 A）

- **A. SVG 矢量图标（已选）**：`cairosvg` 渲染成 `ImageTk`，按状态变色，质感最高。代价：+1 依赖 + 一次性 ~15 个 SVG。
- B. Pillow 手绘图标（零依赖）：代码量大、线条不如 SVG 精致、改图标要改代码。否。
- C. 仅打磨样式不换图标：文字符号质感天花板低，高 DPI 仍糊。否。

## 已确认的默认决定

1. **风格保持对比**（用户已选 A）：选区工具条保持白底浅色（仿飞书），编辑工具栏 + 托盘悬浮菜单保持深底。两者共享同一套图标与 token，仅用各自的 surface 色。
2. **图标风格（用户已选：现成 Lucide）**：从 **Lucide**（`lucide.dev`，ISC 可商用）取现成 SVG，统一改为线性风格——`viewBox="0 0 20 20"`、`stroke-width=2`、`stroke-linecap/linejoin=round`、`fill=none`、单色 `stroke`（文字 T 形等局部用 fill 的例外保留）。逐图标映射到 Lucide 图标名（见下方清单）。SVG 源码**内联进 `assets/icons/*.svg`**（不依赖网络、不进 git LFS）。
3. **依赖**：`cairosvg` 加入 `requirements.txt`。
4. **圆角（用户已选：只容器圆角）**：工具条/菜单的 `wm_overrideredirect` 窗口本身仍是直角矩形（保留 1px 边框），**圆角只做到内部容器**——容器用 `tk.Canvas` 铺一张"圆角矩形 PhotoImage"作底（`create_image` 按窗口尺寸 stretch），其上再摆按钮。四角是 Canvas 的圆角（非窗口裁角），不会露出窗口背后内容。`TB_RADIUS=8`，token 集中控制，观感不佳可退 4px 或直角。
5. **图片按钮尺寸**：图标 18×18，按钮内边距 水平 8 / 垂直 6；操作按钮（图标+文字）文字与图标间距 4。
6. **状态色**（见 token 表）：默认 / hover / 选中 / 禁用 四态，浅色与深色各一套。

## 设计细节

### 新增文件

**`theme.py`** —— 设计 token（模块级常量，三处工具条 import）：

```python
# 字体
FONT = ("Microsoft YaHei UI", 9)
FONT_ICON = ("Microsoft YaHei UI", 10)   # 菜单项等带文字处

# 尺寸
ICON_SIZE = 18          # 图标渲染像素
BTN_PAD_X = 8
BTN_PAD_Y = 6
TB_RADIUS = 8           # 工具条圆角
BTN_RADIUS = 6          # 按钮圆角
SEP_W, SEP_H = 1, 18    # 分隔线

# —— 浅色工具条（选区，仿飞书）——
LIGHT = {
    "surface":  "#ffffff",
    "border":   "#e5e6eb",
    "fg":       "#1f2329",
    "hover":    "#f2f3f5",
    "hover_fg": "#1f2329",
    "active":   "#4a90e2",   # 选中工具
    "active_fg":"#ffffff",
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
    "btn":      "#3a3f47",   # 普通按钮底
    "btn_fg":   "#f5f6f8",
    "btn_hover":"#4a5160",
    "active":   "#4a90e2",
    "active_fg":"#ffffff",
    "primary":  "#4a90e2",   # 主按钮（保存）
    "primary_hover": "#3d7dc9",
    "sep":      "#4a5160",
    "confirm":  "#22c55e",
    "cancel":   "#f5484d",
}

# 强调色（图标默认色 / 选区 active 等共用）
ACCENT = "#4a90e2"
# 预设色板（选区工具条颜色行用，保留现状）
PALETTE = ["#f5484d", "#3370ff", "#86909c",
           "#4e5969", "#ffab00", "#22c55e",
           "#3490ff", "#1f2329", "#a0a6b0", "#ffffff"]
```

> 实现时把 `selector.py` / `editor.py` / `app.py` 里现有的颜色常量改为引用 `theme`，删除重复定义。`PALETTE` 从 `selector.py` 移入 `theme.py`，`selector.py` 改为 `from theme import PALETTE`。

**`icon.py`** —— SVG 图标渲染器：

```python
"""SVG 图标 → ImageTk 渲染（支持重着色 + 缩放 + 缓存）。"""
import io
import tkinter as tk
from PIL import Image
from theme import ICON_SIZE

_cache = {}          # (name, color, size) -> PhotoImage

def render(name: str, color: str, size: int = ICON_SIZE) -> tk.PhotoImage:
    """渲染 assets/icons/<name>.svg 为 PhotoImage，按 color 着色，size×size。

    - 同一 (name,color,size) 只渲染一次，后续命中缓存。
    - 图标文件不存在时返回空白透明图（不抛异常，避免 UI 崩溃）。
    """
    key = (name, color, size)
    if key in _cache:
        return _cache[key]
    svg_path = _icon_path(name)
    img = _svg_to_rgba(svg_path, color, size)
    photo = tk.PhotoImage(data=... )   # 由 PIL Image 转
    _cache[key] = photo
    return photo

def _svg_to_rgba(svg_path, color, size):
    """cairosvg 渲染 svg→bytes，PIL 打开后按 color 重着色（保持 alpha）。"""
    import cairosvg
    png = cairosvg.svg2png(url=svg_path, output_width=size, output_height=size)
    base = Image.open(io.BytesIO(png)).convert("RGBA")
    # 重着色：保留 alpha，RGB 全部替换为 color
    c = _hex_to_rgb(color)
    rgba = base.getdata()
    out = [(*c, a) for (_,_,_,a) in rgba]
    return Image.new("RGBA", base.size, 0).putdata? # 实现细节：用 Image.new + paste 保留 alpha
```

> 实现细节：重着色最稳妥是 `Image.new("RGBA", size, color)` 然后 `Image.composite` 或用 `base.getchannel("A")` 作为 mask 贴到纯色底上。`tk.PhotoImage` 直接吃 PIL 用 `tk.PhotoImage(master=..., data=PIL)` 不支持；需要走 `ImageTk.PhotoImage(pil_img)`（已 import `ImageTk`）。`icon.py` 用 `ImageTk.PhotoImage`。

**`assets/icons/*.svg`** —— 24px 线性图标（viewBox 0 0 20 20，stroke=2，round）。清单（文件名 → 用途 → 用到处）：

| 文件 | 用途 | Lucide 源图标 | 选区 | 编辑 | 托盘 |
|---|---|---|---|---|---|
| `brush.svg` | 画笔 | `brush` | ✓ | ✓ | |
| `rect.svg` | 矩形 | `rectangle` | ✓ | ✓ | |
| `ellipse.svg` | 圆形 | `circle` | ✓ | ✓ | |
| `arrow.svg` | 箭头 | `arrow-right` | ✓ | ✓ | |
| `mosaic.svg` | 打码 | `grid-3x3`（马赛克网格） | ✓ | ✓ | |
| `text.svg` | 文字 | `type` | ✓ | ✓ | |
| `crop.svg` | 裁剪 | `crop` | | ✓ | |
| `palette.svg` | 取色 | `palette` | ✓ | ✓ | |
| `undo.svg` | 撤销 | `undo-2` | ✓ | ✓ | |
| `copy.svg` | 复制 | `copy` | ✓ | ✓ | |
| `save.svg` | 保存 | `save` | ✓ | ✓ | |
| `ocr.svg` | 识别 | `scan-search` | ✓ | ✓ | |
| `confirm.svg` | 确认 | `check` | ✓ | | |
| `cancel.svg` | 取消/关闭/退出 | `x` | ✓ | ✓ | ✓ |
| `region.svg` | 框选截图 | `square-dashed` | | | ✓ |
| `fullscreen.svg` | 全屏截图 | `monitor` | | | ✓ |

> 共 16 个文件（`cancel` 同时服务取消/关闭/退出三处，同一文件）。每个 SVG 由对应 Lucide 图标（24×24 viewBox）改绘为 `0 0 20 20`、`stroke-width=2`、round 端点、`fill=none`，留 2px 安全边距。Lucide 为 ISC 许可，可商用，需在 `assets/icons/README.md` 注明来源与许可。

### 三处工具条改造

**`selector.py`（选区工具条，浅色）**
- 顶部 `from theme import *`，删除本地 `TB_*` / `PALETTE` 常量。
- `_build_toolbar`：按钮从 `tk.Label(text=字符)` 改为 `tk.Label(image=icon.render(name, color))`。
  - 工具按钮：默认 `LIGHT.fg`，hover `LIGHT.hover` 底 + `LIGHT.fg`，选中 `LIGHT.active` 底 + 白图标。
  - 操作按钮（复制/保存/确认/取消）：确认用 `confirm` 绿、取消用 `cancel` 红，其余 `LIGHT.fg`。
  - 颜色按钮（palette.svg）：点击弹色板行（行为不变）；色板行本身仍是色块（非图标）。
  - 撤销/识别/OCR/复制/保存：图片按钮。
- hover/选中逻辑（`_tb_hover` / `_tb_btn`）改为切换 `image=`（不同颜色图标），不再切 `text=`。
- 工具条容器圆角：`tb_win` 内放一个 `tk.Canvas`（铺满，`highlightthickness=0`），Canvas 上 `create_image` 一张按窗口尺寸生成的"圆角矩形 PhotoImage"（底 `LIGHT.surface`、边 1px `LIGHT.border`、圆角 `TB_RADIUS`），按钮 `create_window` 摆到 Canvas 上（或 Canvas 内嵌 Frame 用 `place`）。窗口 `<Configure>` 时重算圆角图尺寸。
- 禁用态（select 模式）：图标渲染 `LIGHT.disabled` 灰。
- 尺寸标签 / 十字准星 / 遮罩：不动。

**`editor.py`（编辑工具栏，深色）**
- 顶部 `from theme import *`，删除本地 `TB_*` 局部常量与类属性色。
- `_build_toolbar`：工具组 7 按钮（brush/rect/ellipse/arrow/mosaic/text/crop）改图片按钮；颜色/背景色按钮保留色块（非图标）；粗细 Spinbox 保留（仅改配色到 token）。
- 操作按钮 `_tb_action_btn`：改为"图标 + 文字"，图标用 `icon.render(name, color)`，文字用 `FONT`；primary（保存）用 `DARK.primary` 底白字 + 白图标。
- hover/选中（`_restool_hover_tb` / `_apply_tool_style`）切 `image=`。
- 工具条容器圆角：同 selector，`toolbar` 内 Canvas + `DARK.surface` 圆角 PhotoImage + `DARK.border` 边框，`<Configure>` 重算。
- 跟随/拖动/最小宽度逻辑：不动。

**`app.py`（托盘悬浮菜单，深色）**
- 顶部 `from theme import *`，删除 `FloatingMenu` 类属性色。
- 菜单项：emoji 文字（📷 🖥 📋 ✕）改为"图标 + 文字"，图标用 `icon.render(region/fullscreen/copy/cancel, DARK.fg)`，hover 时图标切白 + 底 `DARK.hover`。
- 分隔线、布局（padx/pady）改用 token。
- 菜单容器圆角：同深色工具条（Canvas + 圆角 PhotoImage）。

### 状态样式矩阵（统一）

| 状态 | 浅色（选区） | 深色（编辑/菜单） |
|---|---|---|
| 默认底 | `surface` 白 | `btn` `#3a3f47`（普通）/ `surface`（菜单项） |
| 默认图标/文字 | `fg` `#1f2329` | `fg` `#f5f6f8` |
| hover 底 | `hover` `#f2f3f5` | `hover` `#4a90e2` |
| hover 图标 | `fg` | `hover_fg` 白 |
| 选中/active | `active` `#4a90e2` 底 + 白图标 | `active` `#4a90e2` 底 + 白图标 |
| primary（保存） | — | `primary` `#4a90e2` 底 + 白，hover `primary_hover` `#3d7dc9` |
| 确认 | `confirm` 绿 | — |
| 取消/关闭 | `cancel` 红 | `cancel` 红 |
| 禁用 | `disabled` 灰 | `disabled` 灰 |

### PyInstaller spec

`screen-shot.spec` 的 `datas` 增加：

```python
import os
# spec 是普通脚本，__file__ 指向 spec 自身（项目根），不依赖 CWD
_root = os.path.dirname(os.path.abspath(__file__))
_icon_datas = [(os.path.join(_root, 'assets', 'icons'), 'assets/icons')]
_datas = _ocr_datas + _icon_datas
```

`hiddenimports` 增加 `'cairosvg'`（确保其数据文件/子模块被收集）。

### `requirements.txt`

追加一行 `cairosvg`。

## 测试

- 现有 `test_editor.py` / `test_selector.py` 应继续通过（按钮改图片不影响状态机与回调；若测试断言了 `text=` 字符需改为断言 `image=` 或行为）。
- 新增 `test_icon.py`：
  - `render()` 返回 `PhotoImage`，尺寸正确。
  - 不同 color 渲染出不同图（像素差异）。
  - 缓存命中（同 key 返回同对象）。
  - 缺失 svg 时返回空白图不抛异常。
- 新增 `test_theme.py`：`LIGHT`/`DARK` 含必需键，`PALETTE` 长度 10，颜色均为合法 hex。
- 手动验证：`tests/manual_selector.py` 跑真实交互，截图对比三处工具条观感；高 DPI（150%）下确认图标清晰。

## 风险

- **cairosvg 打包**：onefile 下 cairosvg 依赖 cairo 动态库；若 `hiddenimports` 不全，运行时可能缺 .dll。需在 spec 验证；备选：改用 `svglib + reportlab`（纯 Python，无需 cairo 库，渲染略慢但一次性可接受）——实现时先试 cairosvg，打包失败再切 svglib。
- **圆角与 overrideredirect 窗口**：已定为"只容器圆角"——圆角画在内部 Canvas 的 PhotoImage 上，窗口本身仍直角矩形 + 1px 边框，四角不会露出窗口背后内容（最坏情况是四角显示窗口底色，与边框同色，无明显瑕疵）。`<Configure>` 重算圆角图尺寸，拖动跟随时可能每帧重绘，但只是画一张矩形图，开销可接受。
- **图片按钮 hover 重渲染**：hover 切图标时每次 `icon.render` 命中缓存（零渲染），仅 `configure(image=)`，性能无忧。
- **图标风格一致性**：16 个 SVG 需统一 stroke/比例，逐个手绘易不齐；按 Lucide 风格统一模板，必要时用现成 Lucide SVG 路径（CC0 可商用）微调。
- **PhotoImage 内存**：~16 图标 × 2 主题 × 2 色态 ≈ 64 张小图（18×18），缓存内存可忽略。

## 交付顺序建议

1. `theme.py` + `test_theme.py`
2. `assets/icons/*.svg`（16 个）
3. `icon.py` + `test_icon.py`
4. `selector.py` 改造（浅色）
5. `editor.py` 改造（深色）
6. `app.py` 改造（深色菜单）
7. `screen-shot.spec` + `requirements.txt`
8. 全量测试 + 手动高 DPI 验证
