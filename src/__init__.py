"""screen-shot：Windows 截屏工具源码包。

按功能分级：
- app：主入口（托盘 / 全局热键 / Qt 事件循环 / 单实例）
- core：核心逻辑（无 Qt 依赖）
  - capture：截屏、保存、剪贴板
  - config：用户配置读取（%APPDATA%/screen-shot/config.json）
  - applog：滚动文件日志
  - drawing：标注绘制与 OCR
  - screenmap：多显示器 / 高 DPI 坐标映射
- ui：Qt 界面
  - selector：选区窗口门面（组合 region / toolbar / mouse 三个 Mixin）
  - selector_region / selector_toolbar / selector_mouse：选区子逻辑
  - theme / tb_icons / widgets / qtutil：主题常量、工具条图标、辅助窗口、Qt 工具
"""
