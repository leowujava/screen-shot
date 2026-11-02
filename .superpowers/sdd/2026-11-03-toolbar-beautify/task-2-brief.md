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
