"""用户配置：%APPDATA%/screen-shot/config.json（可选文件）。

文件缺失 / JSON 损坏 / 字段非法一律回退默认值，绝不阻断启动。
配置只读（程序不回写），格式与字段说明见 README「配置」一节。

默认值与历史硬编码行为完全一致：
- 热键 Ctrl+Shift+X（框选）/ Ctrl+Shift+S（全屏）/ Ctrl+Shift+Q（退出）
- 保存目录 = 桌面（save_dir 留空）
- 标注颜色 = 色板第一色（红），线宽 3

纯标准库实现（仅依赖 applog），不引入 Qt / PIL，任何模块都可安全导入。
"""
import json
import os
import re

from applog import get_logger

# 默认热键（pynput GlobalHotKeys 语法）
DEFAULT_HOTKEYS = {
    "region": "<ctrl>+<shift>+x",
    "fullscreen": "<ctrl>+<shift>+s",
    "quit": "<ctrl>+<shift>+q",
}

DEFAULT_COLOR = "#f5484d"   # = theme.PALETTE[0]（不 import theme，保持纯标准库）
DEFAULT_WIDTH = 3
_WIDTH_MIN, _WIDTH_MAX = 1, 30   # 与工具条 QSpinBox 范围一致

_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

_CACHE = None


def config_path():
    """配置文件路径：%APPDATA%/screen-shot/config.json（回退 LOCALAPPDATA / ~）。"""
    base = (os.environ.get("APPDATA")
            or os.environ.get("LOCALAPPDATA")
            or os.path.expanduser("~"))
    return os.path.join(base, "screen-shot", "config.json")


def _clean_hotkeys(raw):
    """校验 hotkeys 字段：字符串（空串 = 禁用）；重复组合按固定顺序让位默认值，仍冲突则禁用。

    遍历顺序固定为 DEFAULT_HOTKEYS 的键序（region → fullscreen → quit），
    保证同一份配置在任何机器上解析结果一致。
    显式设为空串 = 禁用该热键；缺失 / 类型非法 = 回退默认。
    """
    result = {}
    used = set()
    for action, default in DEFAULT_HOTKEYS.items():
        if isinstance(raw, dict) and action in raw:
            val = raw[action]
            val = val.strip() if isinstance(val, str) else default
        else:
            val = default
        if val and val in used:
            val = default
        if val and val in used:
            get_logger().warning("热键冲突，禁用 %s：%s", action, val)
            val = ""
        result[action] = val
        if val:
            used.add(val)
    return result


def load_config(path=None):
    """读取并校验配置，返回完整配置 dict。纯函数（不缓存），便于测试。

    缺失文件静默回退默认；损坏 / 非法内容记日志后回退默认。
    逐字段校验：非法字段单独回退，不影响其它合法字段。
    """
    cfg = {
        "hotkeys": dict(DEFAULT_HOTKEYS),
        "save_dir": "",
        "color": DEFAULT_COLOR,
        "width": DEFAULT_WIDTH,
    }
    path = path or config_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return cfg
    except Exception as e:
        get_logger().warning("配置文件不可读，使用默认值（%s）：%s", path, e)
        return cfg
    if not isinstance(raw, dict):
        get_logger().warning("配置文件格式非法（应为 JSON 对象），使用默认值：%s", path)
        return cfg

    cfg["hotkeys"] = _clean_hotkeys(raw.get("hotkeys"))

    save_dir = raw.get("save_dir", "")
    if isinstance(save_dir, str) and save_dir.strip():
        cfg["save_dir"] = os.path.expanduser(save_dir.strip())

    color = raw.get("color", cfg["color"])
    if isinstance(color, str) and _COLOR_RE.match(color.strip()):
        cfg["color"] = color.strip().lower()

    width = raw.get("width", cfg["width"])
    if isinstance(width, int) and not isinstance(width, bool):
        cfg["width"] = max(_WIDTH_MIN, min(_WIDTH_MAX, width))

    return cfg


def get_config():
    """全局配置（惰性加载并缓存，进程生命周期内只读一次文件）。"""
    global _CACHE
    if _CACHE is None:
        _CACHE = load_config()
    return _CACHE
