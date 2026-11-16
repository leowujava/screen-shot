"""文件日志：打包后无控制台（console=False），错误落滚动文件便于现场诊断。

日志位置：%LOCALAPPDATA%/screen-shot/screen-shot.log（1MB × 3 份备份）。
有控制台时（开发运行 python app.py）同时输出到控制台。
日志初始化失败绝不影响主流程。
"""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

_LOGGER = None


def get_logger():
    """返回全局 logger（惰性初始化，只配置一次）。"""
    global _LOGGER
    if _LOGGER is not None:
        return _LOGGER
    logger = logging.getLogger("screen-shot")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        base = os.path.join(
            os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
            "screen-shot")
        os.makedirs(base, exist_ok=True)
        handler = RotatingFileHandler(
            os.path.join(base, "screen-shot.log"),
            maxBytes=1024 * 1024, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    except Exception:
        pass  # 日志目录不可写等异常不阻断启动
    if sys.stdout is not None:
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        logger.addHandler(console)
    _LOGGER = logger
    return logger
