# 代码优化设计（性能 / 去重 / 健壮性）

日期：2026-11-07
状态：待批准

## 意图与背景

screen-shot 是 Windows Python 截屏工具（托盘 + 快捷键 + 仿飞书选区标注）。
本轮是一次全量代码审查后的优化，解决三类问题：

1. 画笔拖动的性能瓶颈（每帧整图 4 倍超采样）
2. 剪贴板编码的 Python 级热点
3. 代码重复（OCR 逻辑、工具函数）与若干健壮性缺陷

用户在 2026-11-07 的对话中确认：四类问题全部处理，采用方案 A
（当前分支一次做完，按主题分 4 个 commit，每组完成后跑全量测试）。

## 目标

### G1 画笔局部超采样（drawing.py）

- 现状：`draw_brush` 每次调用都走 `_supersample_inplace`，在
  **整张图 ×4** 的 RGBA 图层上重绘再缩回。选区标注中画笔是
  每帧（B1-Motion）调用，2000×1200 选区 ≈ 每帧 150MB 分配 + 整图
  LANCZOS 缩放，是卡顿根源。
- 目标：画笔改为**局部超采样**——只在线段包围盒（外加笔宽边距）
  范围内的 4× 图层上绘制，LANCZOS 缩回后 alpha_composite 贴回原图
  对应区域。视觉效果与整图超采样一致（边缘抗锯齿不变）。
- `draw_arrow` / `draw_rect` / `draw_ellipse` 只在松开鼠标时调用一次，
  非热路径，保持整图超采样不动。
- 验收：现有 `tests/test_drawing.py` 全部保持绿色（容差判定不变）；
  新增性能测试：30 段画笔在 2000×1200 图上总耗时显著低于优化前
  （目标 < 1s，优化前实测数十秒级）。

### G2 剪贴板 DIB 编码（capture.py）

- 现状：`_build_dib` 用 Python list 切片循环把整块像素缓冲逐行翻转
  成 bottom-up，每次"复制到剪贴板"都执行。多屏 5760×1440 下约 33MB。
- 目标：改用 C 级操作
  `img.transpose(Image.Transpose.FLIP_TOP_BOTTOM).tobytes("raw", "BGRX")`，
  去掉 Python 循环。BITMAPINFOHEADER 构造不变。
- 验收：`tests/test_capture.py` 绿色；新增单测验证翻转后字节序
  与逐行翻转结果一致（用小图对拍）。

### G3 去重重构

| 项 | 现状 | 目标 |
|---|---|---|
| OCR 引擎 + `ocr_result_to_text` | editor.py 与 drawing.py 各有一份**完全相同**的实现 | 只保留 drawing.py 的 `get_ocr_engine` / `ocr_result_to_text`；editor.py 删除本地实现，改为 `from drawing import ...`（re-export，保证 `from editor import ocr_result_to_text` 等既有导入不破坏，test_ocr.py 不改动） |
| `_alt_pressed` | editor.py 与 selector.py 各有一个相同的静态方法 | 移入 drawing.py 作为模块级函数（纯函数：ctypes 查询 + event.state 回退，无 Tk 依赖），两处改为导入 |
| 文字复制剪贴板 | selector.py OCR 对话框内嵌 `_copy_to_clipboard(s)`，editor.py OCR 对话框内嵌同逻辑 | capture.py 新增 `copy_text_to_clipboard(text) -> bool`（CF_UNICODETEXT，异常返回 False），两处改用 |
| 选区计算 | `Selector._confirm` / `_copy` / `_save` 重复同一段 10 行区域换算 | 提取 `Selector._to_virtual_region()` 辅助方法，三处调用（不能用 `_region()` 命名：与既有实例属性 `self._region` 冲突，builder check 指出） |
| `UndoStack` | list + `pop(0)`（O(n)） | 改 `collections.deque(maxlen=...)`（O(1)，语义不变） |
| 其他小清理 | selector OCR 对话框 `except (tk.TclError, Exception)` 冗余；selector 文字对话框 `grab_set()` 调两次 | 分别改为 `except Exception` / 去掉重复调用 |

验收：`tests/` 全量绿色（不改测试）；grep 确认重复实现只剩一份。

### G4 健壮性修复（TDD：先写失败测试）

1. **真实桌面路径**（capture.py）
   - 现状：`default_save_path` 写死 `~\Desktop`；中文 Windows 桌面是
     `桌面`，目录不存在时 `img.save` 直接抛 FileNotFoundError，保存静默失败。
   - 目标：用 Win32 `SHGetFolderPathW(CSIDL_DESKTOP=0)` 解析真实桌面；
     失败回退 `~\Desktop`。新增 `get_desktop_path()` 便于单测。
   - 测试：mock shell32 返回值验证路径；mock 失败验证回退。
2. **防同秒覆盖**（capture.py）
   - 现状：时间戳精确到秒，同秒内两张截图（如全屏后紧跟框选）后者覆盖前者。
   - 目标：`save_image` 在目标文件已存在时追加 `_1`、`_2`… 后缀
     （`截图_xxx.png` → `截图_xxx_1.png`）。
   - 测试：预创建文件，断言返回新路径且原文件未被覆盖。
3. **文字标注中文字体**（drawing.py `draw_text`）
   - 现状：写死 `arial.ttf`，无 CJK 字形，中文标注渲染成方块。
   - 目标：按 `msyh.ttc → msyh.ttf → simhei.ttf → arial.ttf` 顺序尝试
     （Windows 自带微软雅黑），全部失败再 `load_default()`；
     按 (字体文件, size) 缓存 font 对象，避免每帧重复加载。
   - 测试：断言 Windows 下解析到含 CJK 支持的字体文件；
     无可用字体时回退不抛异常。
4. **复制截图错误提示**（app.py）
   - 现状：`do_copy_capture` 里 `except Exception: pass` 静默吞掉，
     与其他路径（`_safe_copy_to_clipboard` 弹错误框）不一致。
   - 目标：改用 `_safe_copy_to_clipboard`。
   - 测试：现有集成测试保持绿色。
5. **OCR 缺失提示文案**（selector.py / editor.py）
   - 现状：只提示安装 `rapidocr-onnxruntime`，实际优先引擎是 `rapidocr`。
   - 目标：文案同时提及两者（`pip install rapidocr` 或 `pip install rapidocr-onnxruntime`）。
   - 测试：无（纯文案），人工核对。

## 非目标

- 不把 Editor 接入 app.py（当前"确认后只复制到剪贴板"是既有设计）
- 不改 Editor 的"每次编辑自动同步剪贴板"行为（README 已文档化）
- 不动 mss 实例管理、FloatingMenu 焦点策略、工具条跟随轮询等既有架构
- 不做 UI 改版、不新增功能

## 约束

- Windows-only 项目，可自由使用 `ctypes.windll` / win32 API
- 既有测试必须保持绿色；`from editor import ocr_result_to_text` 等
  既有导入路径不得破坏（re-export 兼容）
- 不改变 `Selector` / `Editor` 构造函数与回调签名（app.py 依赖）
- 画笔局部超采样必须保持与整图超采样一致的视觉效果
  （`tests/test_drawing.py` 容差判定为验收基线）
- CI（Python 3.14 + `python -m pytest -q`）必须通过

## 方案（Approaches）

考虑过三种组织方式，用户选定 A：

- **A（选定）**：当前分支一次做完，按 G1→G2→G3→G4 分 4 个 commit，
  每组完成后跑全量测试。理由：master 干净、改动范围清晰，分组 commit
  已提供回滚粒度，CI 会再验证。
- B：分两批（先零行为变化的 G1–G3，再行为修复 G4）。更稳但多一轮
  验证周期，用户不选。
- C：worktree 隔离后合并。当前无未提交改动，隔离对本规模过度，
  用户不选。

## 实现决策（构建者自行决定，记录于此）

- 测试环境：项目内建 `.venv`（已在 .gitignore），安装最小依赖
  `pillow mss pynput pystray pywin32 pytest`；跳过 onnxruntime/rapidocr
  （OCR 测试用假数据，不依赖引擎）。
- G1 局部层范围：线段包围盒外扩 `max(2*笔宽, 8)` 像素，clamp 到图像边界；
  局部层 4× 放大绘制 → LANCZOS 缩回 → alpha_composite 回原图。
  复用现有 `_supersample` 的绘制语义，只改作用区域。
- G3 OCR 去重后 drawing.py 为唯一实现；editor.py 顶部
  `from drawing import get_ocr_engine as _get_ocr_engine, ocr_result_to_text`
  并保留模块级别名，确保旧导入路径可用。
- G4-1 ctypes 签名：`SHGetFolderPathW(None, 0, None, 0x20, buf)`，
  buf 用 `ctypes.create_unicode_buffer(260)`（CSIDL_DESKTOP=0，
  SHGFP_TYPE_CURRENT=0x20）。

## Builder check（2026-11-07）

新构建者通读文档并对照源码逐项核验，结论 "nothing important left"：
- G1 的 8px 边距经核算足以覆盖 LANCZOS 重采样窗口（3px），局部结果与
  整图超采样逐像素一致
- G2 的 transpose 翻转与现有逐行翻转字节级一致（已对拍验证）
- G3 全部重复点属实，re-export 方案不破坏任何既有导入（test_ocr.py、
  test_editor.py、manual_*、app.py 均已核对）
- G4 各项属实，本机 `msyh.ttc` 可按裸文件名解析到 `C:\WINDOWS\fonts`
- 基线：HEAD 处 `python -m pytest -q` → 65 passed, 0 failed

## 遗留交给构建者

- 局部超采样与现有 `_supersample` 的具体代码组织（是否抽公共函数）
- 性能测试的阈值与测量方式（取 30 段中位数或总和）
- 防覆盖后缀的边界（文件被占用时的行为：直接抛原异常即可）
- 字体缓存的数据结构（dict 键设计）
