# 托盘左击菜单移除 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 左击托盘图标直接开始框选截图；右击菜单不变；删除悬浮菜单（FloatingMenu）全部死代码。

**Architecture:** 把 `main()` 里内联的 pystray 菜单提取为模块级 `_build_tray_menu(on_quit)`，使"默认项 = 框选截图 → do_region_capture"可被单元测试；随后删除 `FloatingMenu`、`_tray_icon_rect` 等仅服务于旧左击链路的代码。

**Tech Stack:** Python 3.14 / tkinter / pystray 0.19.5 / pytest（mock）

**Spec:** `docs/superpowers/specs/2026-11-07-tray-left-click-design.md`

## Global Constraints

- pystray 0.19.5：左击图标执行 `default=True` 项；无默认项则左击无操作
- 所有 GUI 必须通过 `_run_on_main` 调度回主线程（`do_region_capture` 已自行处理，无需改动）
- 右击菜单项目与顺序不变：框选截图 / 全屏截图 / 复制截图 / 退出
- 快捷键不变：Ctrl+Shift+X / Ctrl+Shift+S / Ctrl+Shift+Q
- 平台 Windows（`ctypes.windll`）；测试运行 `pytest tests/ -v`

## Review Focus

- 真实 Windows 环境下**左击托盘** → 应立即出现全屏选区窗口（GUI 行为，单测无法覆盖；Task 2 手动验证）
- 左击后立即**再次左击** → 不应叠出第二个选区窗口（Task 2 手动验证）
- **右击**托盘 → 原生菜单 4 项顺序正确、"退出"能正常停托盘并结束进程（Task 1 测试覆盖顺序；Task 2 手动验证退出）
- 选区中按 **Esc** → 取消且进程无异常（既有行为，Task 2 手动验证）
- 删除后 `app.py` 无悬空引用（`pynput.mouse` 仅在 FloatingMenu 内被延迟导入，随之消失）（Task 2 grep 验证）

---

### Task 1: 提取托盘菜单，左击直接框选

**Files:**
- Modify: `app.py`（新增 `_build_tray_menu`；`main()` 内 `run_tray()` 的菜单改为调用它）
- Test: `tests/test_app.py`

**Interfaces:**
- Produces: `_build_tray_menu(on_quit: Callable[[], None]) -> pystray.Menu`（模块级，`app.py`）。菜单含 4 项，"框选截图" 为 `default=True`，其 action 调用 `do_region_capture()`；"退出" action 调用传入的 `on_quit`。
- Consumes: 既有模块级 `do_region_capture()`、`do_fullscreen_capture()`、`do_copy_capture()`（`app.py`）。

- [ ] **Step 1: Write the failing test**

在 `tests/test_app.py` 末尾追加（文件顶部需补 `import app`，现有 import 只有 `from app import process_fullscreen_capture`）：

```python
import app


def test_tray_menu_items_and_default_action():
    with patch("app.do_region_capture") as drc:
        menu = app._build_tray_menu(on_quit=MagicMock())
        items = list(menu)
        assert [i.text for i in items] == ["框选截图", "全屏截图", "复制截图", "退出"]
        defaults = [i for i in items if i.default]
        assert len(defaults) == 1
        defaults[0](None)  # MenuItem 可调用：执行其 action
        drc.assert_called_once_with()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_app.py -v`
Expected: FAIL — `AttributeError: module 'app' has no attribute '_build_tray_menu'`

- [ ] **Step 3: Implement `_build_tray_menu` in `app.py` 并接入 `run_tray()`**

在 `do_copy_capture()` 之后（模块级）新增：

```python
def _build_tray_menu(on_quit):
    """构造托盘菜单。左击（默认项）直接开始框选；右击弹出完整菜单。"""
    return pystray.Menu(
        pystray.MenuItem("框选截图", lambda i, m: do_region_capture(), default=True),
        pystray.MenuItem("全屏截图", lambda i, m: do_fullscreen_capture()),
        pystray.MenuItem("复制截图", lambda i, m: do_copy_capture()),
        pystray.MenuItem("退出", lambda i, m: on_quit()),
    )
```

`main()` 内 `run_tray()` 改为使用它（原内联 4 项菜单删除）：

```python
    def run_tray():
        icon = pystray.Icon(
            "screen-shot",
            _make_tray_icon_image(),
            "截图工具",
            _build_tray_menu(on_quit),
        )
        tray_holder["icon"] = icon
        icon.run()
```

注意：本任务**不删** `FloatingMenu` / `_tray_icon_rect` / `_get_menu` / `_show_floating_menu`（Task 2 删除）；`_show_floating_menu` 至此无任何调用方。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_app.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_app.py
git commit -m "feat: 托盘左击直接框选截图（默认项改调 do_region_capture），菜单提取为 _build_tray_menu"
```

---

### Task 2: 删除悬浮菜单死代码并整体验证

**Files:**
- Modify: `app.py`（删除 `FloatingMenu` 类、`_tray_icon_rect()`，`main()` 内 `_menu_holder`、`_get_menu()`、`_show_floating_menu()`）

**Interfaces:**
- Consumes: Task 1 的 `_build_tray_menu`（删除后确认仍被 `run_tray()` 使用）
- Produces: 无新增接口；`app` 模块不再含 `FloatingMenu`、`_tray_icon_rect`

- [ ] **Step 1: 删除死代码**

从 `app.py` 删除以下整块：

1. 模块级 `FloatingMenu` 类（现约 187–290 行）
2. 模块级 `_tray_icon_rect()`（现约 134–174 行）
3. `main()` 内 `_menu_holder = {}`（现约 303 行）
4. `main()` 内 `_get_menu()` 函数（现约 324–334 行）
5. `main()` 内 `_show_floating_menu()` 函数（现约 336–350 行）

行号以删除时实际位置为准（Task 1 已改动过文件）。删除后 `main()` 内 `on_quit`、`run_tray`、热键注册等保持不变。

- [ ] **Step 2: 验证无悬空引用且模块可导入**

Run:
```bash
rg -n "FloatingMenu|_tray_icon_rect|_show_floating_menu|_get_menu|_menu_holder" app.py tests/
python -c "import app"
```
Expected: `rg` 无匹配（退出码 1）；`python -c "import app"` 无异常、无输出

- [ ] **Step 3: 运行全部测试**

Run: `pytest tests/ -v`
Expected: 全部 PASS（含 Task 1 的新测试）

- [ ] **Step 4: 手动验证（Windows 实机）**

运行 `python app.py`，逐项确认并记录结果：

1. **左击**托盘图标 → 立即出现全屏选区窗口，框选后正常复制/保存
2. 选区出现前**再次左击** → 不叠出第二个选区窗口
3. 选区中按 **Esc** → 取消，进程无异常
4. **右击**托盘 → 原生菜单，4 项顺序：框选截图 / 全屏截图 / 复制截图 / 退出；点"退出"托盘消失且进程结束

- [ ] **Step 5: Commit**

```bash
git add app.py
git commit -m "refactor: 删除悬浮菜单死代码（FloatingMenu/_tray_icon_rect/_show_floating_menu/_get_menu）"
```
