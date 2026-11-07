# 托盘左击菜单移除 - 设计

日期：2026-11-07
状态：待确认

## 意图 / 为什么

当前左击托盘图标会弹出一个自绘悬浮菜单（FloatingMenu），需要点两下才能执行动作，
交互绕。目标是让左击一步直达截图，菜单只保留给右击。

## 目标

- 左击托盘图标 → 直接开始框选截图（`do_region_capture`）
- 右击托盘图标 → 原生 pystray 菜单不变（框选截图 / 全屏截图 / 复制截图 / 退出）

## 非目标 / 反目标

- 不改快捷键（Ctrl+Shift+X / S / Q）
- 不改右击菜单的项目与顺序
- 不做"左击无操作"方案（用户已否决）
- 不新增菜单项

## 约束

- pystray 0.19.5：左击执行 `default=True` 项；无默认项则左击无操作
- 所有 GUI 必须调度回主线程（`_run_on_main`）——`do_region_capture` 已自行处理线程，无需改动

## 方案对比

- **A. 左击 = 直接框选（已选）**：默认项动作从 `_show_floating_menu()` 改为
  `do_region_capture()`。一步直达，符合截图工具惯例（Snipaste 等）。
- B. 左击 = 无操作：去掉 `default=True`。左击失去意义，用户可能困惑。已否决。

## 改动内容（`app.py`）

1. `run_tray()` 中默认项动作：`lambda i, m: _show_floating_menu()` → `lambda i, m: do_region_capture()`
2. 删除以下死代码（仅被左击菜单链路使用，测试无引用）：
   - `FloatingMenu` 类（约 100 行）
   - `main()` 内 `_get_menu()`、`_show_floating_menu()`、`_menu_holder`
   - 模块级 `_tray_icon_rect()`（约 40 行 Win32 枚举）

## 留给实现者的

- 删除后确认 `app.py` 无残留 import / 未用引用
- 手动验证：左击出选区、右击出菜单、Esc 取消选区、退出正常
