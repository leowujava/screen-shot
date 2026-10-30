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
| Ctrl+Shift+X | 框选区域截图（复制到剪贴板 + 带编辑） |
| Ctrl+Shift+S | 全屏截图（直接保存 + 复制到剪贴板） |
| Ctrl+Shift+Q | 退出 |

## 托盘

- 左键 / 右键：在图标位置弹出悬浮菜单（框选 / 全屏 / 复制 / 退出）
- 悬浮菜单为无边框置顶窗口，点击外部或按 Esc 关闭

## 编辑器

画笔、矩形、箭头、打码（马赛克）、文字、裁剪、撤销（Ctrl+Z）、复制（Ctrl+C）、保存（Ctrl+S）、识别文字（OCR）。

## 识别文字（OCR）

- 编辑器工具栏"🔍 识别文字"按钮：识别图中文字，弹窗显示（按阅读顺序排列），可一键"复制全部"到剪贴板。
- 支持中英文混合识别（基于 `rapidocr` / PP-OCRv6，已加入 requirements.txt），无需安装 Tesseract。

## 剪贴板

- 框选截图（Ctrl+Shift+X）：选中区域后自动复制到剪贴板，可直接粘贴（Ctrl+V），同时打开编辑器
- 全屏截图（Ctrl+Shift+S）：保存后自动复制到剪贴板，可直接粘贴（Ctrl+V）
- 托盘"复制截图"：只复制到剪贴板，不保存
- 编辑器：每次编辑（画笔 / 矩形 / 箭头 / 打码 / 文字 / 撤销）完成后自动同步到剪贴板，可直接粘贴（Ctrl+V）；工具栏"复制"按钮或 Ctrl+C 也可手动复制

## 保存位置

默认：桌面 `截图_YYYYMMDD_HHMMSS.png`

## 打包为可执行文件

使用 PyInstaller 打成单文件、无控制台的 Windows 可执行文件：

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name screen-shot ^
  --hidden-import pystray._win32 ^
  --hidden-import pynput._util.win32 ^
  --hidden-import pynput.keyboard._win32 ^
  --hidden-import pynput.mouse._win32 ^
  --hidden-import mss.windows ^
  --hidden-import mss.windows.gdi ^
  --hidden-import onnxruntime ^
  --hidden-import numpy ^
  app.py
```

生成产物：`dist\screen-shot.exe`（约 30 MB，可直接双击运行，无需安装 Python）。
