"""三处工具条共享的设计 token（颜色、字体、间距、圆角）。"""
import tkinter as tk
from PIL import Image, ImageTk


# 字体
FONT = ("Microsoft YaHei UI", 9)
FONT_ICON = ("Microsoft YaHei UI", 10)   # 菜单项等带文字处

# 尺寸
ICON_SIZE = 18          # 图标渲染像素
BTN_PAD_X = 8
BTN_PAD_Y = 6
TB_RADIUS = 8           # 工具条容器圆角
BTN_RADIUS = 6          # 按钮圆角（本期仅 token）
SEP_W, SEP_H = 1, 18    # 分隔线

# —— 浅色工具条（选区，仿飞书）——
LIGHT = {
    "surface":  "#ffffff",
    "border":   "#e5e6eb",
    "fg":       "#1f2329",
    "hover":    "#f2f3f5",
    "hover_fg": "#1f2329",
    "active":   "#4a90e2",
    "active_fg": "#ffffff",
    "disabled": "#c9cdd4",
    "sep":      "#e5e6eb",
}

# —— 深色工具条（编辑器 / 托盘菜单）——
DARK = {
    "surface":  "#2b2f36",
    "border":   "#4a5160",
    "fg":       "#f5f6f8",
    "hover":    "#4a90e2",
    "hover_fg": "#ffffff",
    "btn":      "#3a3f47",
    "btn_fg":   "#f5f6f8",
    "btn_hover": "#4a5160",
    "active":   "#4a90e2",
    "active_fg": "#ffffff",
    "primary":  "#4a90e2",
    "primary_hover": "#3d7dc9",
    "sep":      "#4a5160",
    "confirm":  "#22c55e",
    "cancel":   "#f5484d",
}

# 强调色（图标默认色 / 选区 active 等共用）
ACCENT = "#4a90e2"
# 预设色板（选区工具条颜色行用；第一色 = 默认画图色）
PALETTE = ["#f5484d", "#3370ff", "#86909c",
           "#4e5969", "#ffab00", "#22c55e",
           "#3490ff", "#1f2329", "#a0a6b0", "#ffffff"]


def _hex_to_rgb(color: str) -> tuple:
    """#rgb 或 #rrggbb → (r, g, b)。"""
    v = color.lstrip("#")
    if len(v) == 3:
        v = "".join(ch * 2 for ch in v)
    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)


def rounded_rect_photo(master, w, h, radius, fill, outline) -> "tk.PhotoImage":
    """生成 w×h 的圆角矩形 PhotoImage（fill 底 + outline 1px 边框）。

    用于工具条/菜单容器背景（Canvas 铺底，规避 tkinter 直角限制）。
    返回的 PhotoImage 由 master 持有，生命周期随 master。
    """
    if w < 2 or h < 2:
        # 退化：太小的窗口不画圆角（避免 PIL 报错）
        img = Image.new("RGBA", (max(1, w), max(1, h)),
                        (*_hex_to_rgb(fill), 255))
        return ImageTk.PhotoImage(img, master=master)
    r = max(0, min(radius, w // 2, h // 2))
    f, o = _hex_to_rgb(fill), _hex_to_rgb(outline)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    from PIL import ImageDraw
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, w - 1, h - 1), radius=r, fill=(*f, 255),
                        outline=(*o, 255) if outline else None, width=1)
    return ImageTk.PhotoImage(img, master=master)
