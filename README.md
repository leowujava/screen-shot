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
| Ctrl+Shift+X | 框选区域截图（放开后可标注，确认后复制到剪贴板） |
| Ctrl+Shift+S | 全屏截图（保存到桌面 + 复制到剪贴板） |
| Ctrl+Shift+Q | 退出 |

## 托盘

- 左键点击图标：直接触发框选截图
- 右键点击图标：弹出系统菜单（框选截图 / 全屏截图 / 复制截图 / 退出）

## 选区标注（框选放开后）

框选截图时，松开鼠标后选区**不会立即结束**，而是进入**调整/标注模式**：

- 框选过程中显示实时桌面；首次框选完成时才捕获并定格画面，之后可移动或调整选区
- 选区周围出现 **8 个调整手柄**（白色方块），可拖动手柄缩放选区
- 拖动选区内部可**整体移动**选区（选区外区域暗化，选区内保留原图亮度）
- 选区下方出现**白底工具条**（放不下时翻到选区上方），含：
  - 标注工具：移动选区 / 矩形 / 圆形 / 箭头 / 画笔 / 文字 / 打码
  - 颜色（单击弹出预设色板，双击打开系统取色器）与线宽
  - 撤销（Ctrl+Z）
  - 识别文字（OCR）
  - 复制 / 保存 / 取消 / 确认
- 框选过程中十字准星跟随鼠标，并显示实时尺寸（宽 × 高）
- 已有标注后选区锁定（手柄不可拖动），避免误触改选区

**确认 / 取消：**

| 操作 | 效果 |
|---|---|
| `Enter` / 双击 | 确认选区，复制到剪贴板（不自动保存） |
| `Esc` / 右键 | 取消 |
| 工具条"复制" | 复制到剪贴板 |
| 工具条"保存" | 保存到桌面 |
| 工具条"✓ 确认" | 确认（复制到剪贴板） |
| 工具条"✕ 取消" | 取消 |

> `editor.py` 保留了一个独立的 PySide6 编辑器窗口（保存 / 复制 / OCR），
> 供手动测试（`tests/manual_editor.py`）与后续复用，主流程不会自动打开。

## 识别文字（OCR）

- 选区工具条"识别文字"按钮：识别选区内文字，弹窗显示（按阅读顺序排列），可"复制选中 / 复制全部"到剪贴板
- 独立编辑器（`editor.py`）的"识别文字"按钮行为一致
- OCR 在工作线程执行，识别期间界面不卡死
- 支持中英文混合识别（基于 `rapidocr` / PP-OCRv6，已加入 requirements.txt），无需安装 Tesseract

## 剪贴板

- 框选截图（Ctrl+Shift+X）：确认选区后自动复制到剪贴板，可直接粘贴（Ctrl+V）
- 全屏截图（Ctrl+Shift+S）：保存后自动复制到剪贴板，可直接粘贴（Ctrl+V）
- 托盘"复制截图"：只复制到剪贴板，不保存
- 复制失败（剪贴板被占用等）会弹出错误提示，不会静默失败

## 保存位置

默认：桌面 `截图_YYYYMMDD_HHMMSS.png`（同名已存在时自动追加 `_1`、`_2` 后缀，不覆盖）

## 自动发布（GitHub Actions）

工作流：`.github/workflows/build-release.yml`（Windows runner，Python 3.14 + PyInstaller）。

| 触发 | 结果 |
|---|---|
| 推送到 `master` / `main` | 跑测试 → 打包 → 更新滚动预发布 `latest` |
| 推送 `v*` 标签（如 `v1.0.0`） | 跑测试 → 打包 → 发布正式 Release（自动生成 release notes） |
| 手动触发（Actions → Build & Release → Run workflow） | 同分支推送，更新 `latest` |

每次构建都会生成两个附件：`screen-shot.exe` 与 `screen-shot.exe.sha256`，Actions 页面同时保留 30 天的构建产物。

发布正式版本：

```bash
git tag v1.0.0
git push origin v1.0.0
```

前提：仓库 `Settings → Actions → General → Workflow permissions` 需选择 **Read and write permissions**，否则工作流无法创建 Release。

下载地址：`https://github.com/<owner>/<repo>/releases/tag/latest`（滚动预发布）或 `<tag>` 对应的正式版本。

## 打包为可执行文件

使用 PyInstaller 打成单文件、无控制台的 Windows 可执行文件（推荐直接用 `screen-shot.spec`）：

```bash
pip install pyinstaller
pyinstaller screen-shot.spec --noconfirm --clean
```

生成产物：`dist\screen-shot.exe`（约 120 MB，含 OCR 模型与 onnxruntime，可直接双击运行，无需安装 Python）。
