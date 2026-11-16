"""pytest 全局配置：Qt 测试一律走 offscreen 平台，不弹真实窗口。"""
import os

# 必须在任何 QApplication 创建之前设置
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="session")
def qt_app():
    """整个测试会话共享一个 QApplication（Qt 要求同进程只有一个）。"""
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """配置指向不存在的临时路径并清空缓存：测试不受真实用户配置影响。"""
    from src.core import config
    monkeypatch.setattr(config, "_CACHE", None)
    monkeypatch.setattr(config, "config_path",
                        lambda: str(tmp_path / "config.json"))
    yield
