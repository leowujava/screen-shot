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
