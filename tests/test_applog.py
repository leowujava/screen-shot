"""applog 文件日志测试。"""
import logging
from unittest.mock import patch

import pytest

import applog


@pytest.fixture
def clean_logger(monkeypatch):
    """隔离全局 logger 状态：测试前后清空 handlers，避免跨测试重复挂载。"""
    monkeypatch.setattr(applog, "_LOGGER", None)
    lg = logging.getLogger("screen-shot")
    for h in list(lg.handlers):
        h.close()
        lg.removeHandler(h)
    yield lg
    for h in list(lg.handlers):
        h.close()
        lg.removeHandler(h)


def test_logger_writes_to_localappdata(tmp_path, monkeypatch, clean_logger):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    log = applog.get_logger()
    log.error("诊断消息")
    for h in log.handlers:
        h.flush()
    p = tmp_path / "screen-shot" / "screen-shot.log"
    assert p.exists()
    assert "诊断消息" in p.read_text(encoding="utf-8")
    assert applog.get_logger() is log  # 只初始化一次


def test_logger_survives_unwritable_dir(clean_logger):
    """日志目录不可写：get_logger 仍返回可用 logger，不抛异常。"""
    with patch.object(applog.os, "makedirs", side_effect=OSError("denied")):
        log = applog.get_logger()
    log.error("仍然可用")  # 不抛异常
