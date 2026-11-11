import threading
from unittest.mock import MagicMock, patch

from PIL import Image

from app import process_fullscreen_capture


def test_process_fullscreen_saves_image():
    img = MagicMock()
    with patch("app.save_image") as si, patch("app.copy_to_clipboard") as cc:
        si.return_value = "C:\\shot.png"
        path = process_fullscreen_capture(img)
        assert path == "C:\\shot.png"
        si.assert_called_once_with(img, None)
        cc.assert_called_once_with(img)


def test_do_copy_capture_uses_safe_copy():
    """复制截图应走 _safe_copy_to_clipboard（失败有提示），不再裸吞异常。"""
    import app

    done = threading.Event()
    mock_safe = MagicMock(side_effect=lambda img: done.set())

    def fake_capture():
        return Image.new("RGB", (10, 10))

    with patch.object(app, "capture_fullscreen", fake_capture), \
         patch.object(app, "_safe_copy_to_clipboard", mock_safe):
        app.do_copy_capture()
        assert done.wait(5)
    mock_safe.assert_called_once()


def test_do_copy_capture_reports_clipboard_failure():
    """剪贴板打开失败时，应弹出错误对话框而不是崩溃或静默。"""
    import app

    shown = threading.Event()
    mock_err = MagicMock(side_effect=lambda *a, **k: shown.set())

    def fake_capture():
        return Image.new("RGB", (10, 10))

    with patch.object(app, "capture_fullscreen", fake_capture), \
         patch.object(app, "copy_to_clipboard", side_effect=RuntimeError("clipboard busy")), \
         patch.object(app.QMessageBox, "critical", mock_err):
        app.do_copy_capture()
        assert shown.wait(5)
    mock_err.assert_called_once()

