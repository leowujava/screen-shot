# screen-shot 截屏工具设计

## 概述

Windows 桌面截屏工具，Python 实现。支持框选区域截图（带编辑）和全屏截图（直接保存），系统托盘常驻。

## 模块设计

| 模块 | 职责 |
|---|---|
| `app.py` | 入口。全局快捷键注册（pywin32），截屏流程编排，托盘图标（pystray） |
| `capture.py` | mss 截屏：全屏、指定区域、指定显示器，返回 PIL Image |
| `selector.py` | tkinter 全屏透明覆盖层：拖拽框选、十字准星、尺寸提示、Esc 取消、右键取消 |
| `editor.py` | tkinter 编辑窗口：工具栏 + 画布，工具见下 |

## 快捷键

| 键 | 动作 |
|---|---|
| `Ctrl+Shift+A` | 框选区域 → 编辑器 |
| `Ctrl+Shift+F` | 全屏截图 → 直接保存 |
| `Ctrl+Shift+Q` | 退出程序 |

## 数据流

```
Ctrl+Shift+A → capture 全屏 → selector 覆盖层选区 → editor 打开（含选区图）→ 保存
Ctrl+Shift+F → capture 全屏 → 直接保存
```

## 编辑器工具

- **画笔**：可拖拽绘制，颜色/粗细可调
- **矩形**：拖拽画矩形框
- **箭头**：拖拽画箭头
- **打码**：拖拽区域马赛克（8px 块）
- **文字**：点击位置弹出输入框，输入后以红字显示
- **撤销**：Ctrl+Z，保留 10 步
- **保存**：Ctrl+S，保存为 PNG

## 保存规则

- 默认路径：`%USERPROFILE%\Desktop\截图_YYYYMMDD_HHMMSS.png`
- 编辑器可另存为（选路径/格式）
- 保存失败弹窗提示

## 托盘

- pystray 图标，左键 = 框选截图
- 右键菜单：框选截图 / 全屏截图 / 退出
- 图标用 Pillow 动态生成（相机 emoji 不可靠，用简单图形）

## 多显示器

- mss 获取虚拟桌面全尺寸
- selector 覆盖层覆盖全部虚拟桌面（`geometry` 从 (-x, -y) 开始）
- 坐标换算：tkinter 事件坐标 → 虚拟桌面坐标

## 依赖

```
mss
Pillow
pynput
pystray
pywin32
```

## 测试

- `test_capture.py`：验证截屏输出尺寸/内容正确
- 手动验证：快捷键触发、选区交互、编辑各工具、保存路径

## 不做的事

- 不做定时截图
- 不做 OCR
- 不做历史记录列表
- 不做开机自启
- 不做打包（直接 `python app.py` 运行）
