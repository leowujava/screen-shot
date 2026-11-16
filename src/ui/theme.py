"""选区界面配色与显示常量（overlay / 工具条 / 图标共享）。

从 selector.py 拆出的纯常量模块，无 Qt 依赖；
tb_icons / widgets / selector 共同引用，避免循环导入。
"""

# 工具条配色（白底 + 蓝色高亮，仿飞书截图工具条）
TB_BG = "#ffffff"
TB_FG = "#1f2329"
TB_HOVER = "#f2f3f5"        # 按钮 hover 浅灰
TB_SEP = "#e5e6eb"
TB_DISABLED = "#c9cdd4"     # 禁用（select 模式）
TB_ACTIVE = "#4a90e2"       # 选中工具（蓝圈/蓝字）

# 确认 / 取消 图标颜色
TB_CONFIRM_FG = "#22c55e"
TB_CANCEL_FG = "#f5484d"

# 预设色板（仿工具条下方弹出的颜色行；第一色 = 默认画图色，截图箭头红）
PALETTE = ["#f5484d", "#3370ff", "#86909c",
           "#4e5969", "#ffab00", "#22c55e",
           "#3490ff", "#1f2329", "#a0a6b0", "#ffffff"]

# 尺寸标签配色
LABEL_BG = "#2b2f36"
LABEL_FG = "#f5f6f8"

# 选区边框配色：白色外线 + 深色内线（双线）。
# 纯白边框在白色截图背景上不可见，双线保证任意背景下至少一条线可见。
BORDER_OUTLINE = "white"
BORDER_INNER = "#4e5969"

# 十字准星颜色
CROSSHAIR_COLOR = "red"
CROSSHAIR_WIDTH = 1

# 暗化遮罩 alpha（0-255，128 ≈ 50%）
MASK_ALPHA = 128

# 无定格截图时的窗口透明度（半透明黑底透出桌面暗化效果）
DIM_WINDOW_OPACITY = 0.5
FULL_WINDOW_OPACITY = 1.0
