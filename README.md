# screen-shot

Windows Python 截屏工具。

## 安装

```bash
pip install -r requirements.txt
```

## 运行

```bash
python app.py
# 等价方式（项目根目录下）
python -m src
```

## 目录结构

源码直接在 `src/` 下，按功能分级（`src` 本身即源码包）：

```
screen-shot/
├── app.py                  # 薄入口（兼容 python app.py / PyInstaller）
├── src/                    # 源码包
│   ├── app.py              # 主入口：托盘 / 全局热键 / Qt 事件循环 / 单实例
│   ├── __main__.py         # 支持 python -m src
│   ├── version.py          # 版本号（唯一事实来源）
│   ├── core/               # 核心逻辑（无 Qt 依赖）
│   │   ├── capture.py      # 截屏、保存、剪贴板
│   │   ├── config.py       # 用户配置读取
│   │   ├── applog.py       # 滚动文件日志
│   │   ├── drawing.py      # 标注绘制与 OCR
│   │   └── screenmap.py    # 多显示器 / 高 DPI 坐标映射
│   ├── ui/                 # Qt 界面
│   │   ├── selector.py     # 选区窗口门面（组合下面三个 Mixin）
│   │   ├── selector_region.py  # 选区几何 / 裁剪 / 撤销预算
│   │   ├── selector_toolbar.py # 工具条构建与交互
│   │   ├── selector_mouse.py   # 鼠标状态机 / 标注绘制
│   │   ├── theme.py        # 主题常量与色板
│   │   ├── tb_icons.py     # 工具条图标绘制
│   │   ├── widgets.py      # 工具条 / OCR 结果弹窗
│   │   └── qtutil.py       # Qt 通用工具
│   └── tools/editor.py     # 独立编辑器窗口（主流程不自动打开）
├── tests/                  # pytest 测试与手动脚本
├── docs/                   # 设计与计划文档
├── requirements.txt        # 依赖清单
├── pyproject.toml          # pytest / ruff 配置（pythonpath 指向项目根）
├── version_info.txt        # exe 的 Windows 版本资源（与 src/version.py 同步）
└── screen-shot.spec        # PyInstaller 打包脚本
```

## 快捷键

| 键 | 动作 |
|---|---|
| Ctrl+Shift+X | 框选区域截图（放开后可标注，确认后复制到剪贴板） |
| Ctrl+Shift+S | 全屏截图（弹"另存为"选择保存路径 + 复制到剪贴板） |
| Ctrl+Shift+Q | 退出 |

> 快捷键可通过配置文件自定义（见下文「配置」）。

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
- 框选过程中十字准星跟随鼠标，并显示实时尺寸（宽 × 高，按输出物理像素显示）
- 已有标注后选区锁定（手柄不可拖动），避免误触改选区

## 高 DPI / 多显示器

- 支持 Windows 125% / 150% 等显示缩放：界面按逻辑像素布局，选区从物理
  截图裁剪，输出保持屏幕原始分辨率（150% 下框选 100×100 逻辑像素 →
  150×150 输出图片）
- 多显示器：overlay 覆盖整个虚拟桌面，按显示器逐屏映射；混合 DPI 下
  选区输出按所在屏幕的物理像素裁剪，显示底图为逐屏缩放后的逻辑画面
- 多屏缩放比例不同时，单窗口显示在逻辑矩形重叠区域只能呈现其中一块屏，
  但选区输出不受影响

**确认 / 取消：**

| 操作 | 效果 |
|---|---|
| `Enter` / 双击 | 确认选区，复制到剪贴板（不自动保存） |
| `Esc` / 右键 | 取消 |
| 工具条"复制" | 复制到剪贴板 |
| 工具条"保存" | 弹"另存为"对话框选择保存路径（取消则不保存） |
| 工具条"✓ 确认" | 确认（复制到剪贴板） |
| 工具条"✕ 取消" | 取消 |

> `src/tools` 保留了一个独立的 PySide6 编辑器窗口（保存 / 复制 / OCR），
> 供手动测试（`tests/manual_editor.py`）与后续复用，主流程不会自动打开。

## 识别文字（OCR）

- 选区工具条"识别文字"按钮：识别选区内文字，弹窗显示（按阅读顺序排列），可"复制选中 / 复制全部"到剪贴板
- 独立编辑器（`src/tools`）的"识别文字"按钮行为一致
- OCR 在工作线程执行，识别期间界面不卡死
- 支持中英文混合识别（基于 `rapidocr` / PP-OCRv6，已加入 requirements.txt），无需安装 Tesseract

## 剪贴板

- 框选截图（Ctrl+Shift+X）：确认选区后自动复制到剪贴板，可直接粘贴（Ctrl+V）
- 全屏截图（Ctrl+Shift+S）：保存（或取消保存）后自动复制到剪贴板，可直接粘贴（Ctrl+V）
- 托盘"复制截图"：只复制到剪贴板，不保存
- 剪贴板被其他程序短暂占用时自动重试（约 0.35s），仍失败才弹出错误提示，不会静默失败

## 保存位置

全屏截图（Ctrl+Shift+S / 托盘"全屏截图"）与选区工具条"保存"都会弹出
**"另存为"对话框**，可自行选择保存路径：

- 默认目录：上一次选中的目录；首次为配置 `save_dir`（留空 = 桌面，见「配置」）
- 默认文件名：`截图_YYYYMMDD_HHMMSS.png`
- 选中已有文件时由对话框确认覆盖，确认后写入该确切路径
- 取消对话框则不保存；全屏截图取消后仍会复制到剪贴板（可直接 Ctrl+V）
- 保存失败只弹提示，不阻断剪贴板复制

## 配置

可选配置文件：`%APPDATA%\screen-shot\config.json`（不存在则全部使用默认值；
程序只读、不回写）。完整示例（各字段均可省略）：

```json
{
  "hotkeys": {
    "region": "<ctrl>+<shift>+x",
    "fullscreen": "<ctrl>+<shift>+s",
    "quit": "<ctrl>+<shift>+q"
  },
  "save_dir": "",
  "color": "#f5484d",
  "width": 3
}
```

| 字段 | 说明 | 默认 |
|---|---|---|
| `hotkeys.region` | 框选截图热键 | Ctrl+Shift+X |
| `hotkeys.fullscreen` | 全屏截图热键 | Ctrl+Shift+S |
| `hotkeys.quit` | 退出热键 | Ctrl+Shift+Q |
| `save_dir` | 保存目录（"另存为"对话框的首次默认目录；留空 = 桌面，支持 `~`） | 桌面 |
| `color` | 默认标注颜色（`#rrggbb`） | 色板第一色（红） |
| `width` | 默认线宽（1–30，越界自动收敛） | 3 |

- 热键语法同 pynput（如 `<ctrl>+<alt>+a`）；设为空串 `""` 禁用该热键
- 两个动作设成同一组合时，按 region → fullscreen → quit 顺序后者让位默认值，仍冲突则禁用
- 文件缺失 / JSON 损坏 / 字段非法一律回退默认值（记日志），绝不阻断启动；
  热键组合无法解析时整体回退默认热键
- 配置在启动时读取一次，修改后需重启生效

## 单实例与日志

- 同一时间只运行一个实例：已在运行时再次启动会直接退出（不叠托盘图标/热键）
- 错误日志：`%LOCALAPPDATA%\screen-shot\screen-shot.log`（滚动，1MB × 3 份备份）；
  带控制台运行时同时输出到控制台

## 自动发布（GitHub Actions）

工作流：`.github/workflows/build-release.yml`（Windows runner，Python 3.14 + PyInstaller）。
构建前先跑 ruff lint（配置见 `pyproject.toml`）与 pytest。

| 触发 | 结果 |
|---|---|
| 推送到 `master` / `main` | lint + 测试 → 打包 → 更新滚动预发布 `latest` |
| 推送 `v*` 标签（如 `v1.0.0`） | lint + 测试 → 打包 → 发布正式 Release（自动生成 release notes） |
| 手动触发（Actions → Build & Release → Run workflow） | 同分支推送，更新 `latest` |

每次构建都会生成两个附件：`screen-shot.exe` 与 `screen-shot.exe.sha256`，Actions 页面同时保留 30 天的构建产物。

发布正式版本：

```bash
# 先更新版本号（两处必须一致，tests/test_version.py 守护）
#   src/version.py        __version__ = "1.1.0"
#   version_info.txt      FileVersion / ProductVersion / filevers
git tag v1.1.0
git push origin v1.1.0
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
