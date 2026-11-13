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


def test_open_region_selector_ignores_second_while_active():
    """防叠窗：已有未结束选区时，再次触发不得新建第二个 overlay。"""
    import app

    app._selector_holder.clear()
    try:
        with patch.object(app, "Selector") as mock_sel:
            mock_sel.return_value = MagicMock(_finished=False)
            app.open_region_selector(0, 0, (1920, 1080))
            app.open_region_selector(0, 0, (1920, 1080))
            assert mock_sel.call_count == 1
    finally:
        app._selector_holder.clear()


def test_open_region_selector_cleans_finished_and_reopens():
    """已结束的历史选区被清理；结束后可再次正常打开新选区。"""
    import app

    old = MagicMock(_finished=True)
    app._selector_holder.clear()
    app._selector_holder.append(old)
    try:
        with patch.object(app, "Selector") as mock_sel:
            mock_sel.return_value = MagicMock(_finished=False)
            app.open_region_selector(0, 0, (1920, 1080))
            assert mock_sel.call_count == 1
    finally:
        app._selector_holder.clear()


def test_selector_copy_save_callbacks_ignore_cancel():
    """取消（region=None）时复制/保存回调不执行任何动作。"""
    import app

    with patch.object(app, "_safe_copy_to_clipboard") as mock_copy, \
         patch.object(app, "save_image") as mock_save:
        app._on_copy(None, None)
        app._on_save(None, None)
        mock_copy.assert_not_called()
        mock_save.assert_not_called()


def test_on_save_runs_in_worker_thread():
    """选区保存：PNG 编码在工作线程执行（不卡主线程事件循环）。"""
    import app

    done = threading.Event()
    info = {}

    def fake(img, path=None):
        info["t"] = threading.get_ident()
        done.set()
        return "C:\\shot.png"

    with patch.object(app, "save_image", fake):
        main_id = threading.get_ident()
        app._on_save((0, 0, 10, 10), Image.new("RGB", (10, 10)))
        assert done.wait(5)
    assert info["t"] != main_id, "save_image 不应在主线程执行"


def test_on_save_reports_failure_without_raising():
    """选区保存失败：弹提示而非抛异常（异常不再沿 _exit→_finish 传播到
    Qt 事件循环导致整个应用崩溃）。"""
    import app

    shown = threading.Event()
    mock_err = MagicMock(side_effect=lambda *a, **k: shown.set())
    with patch.object(app, "save_image", side_effect=OSError("disk full")), \
         patch.object(app.QMessageBox, "critical", mock_err):
        app._on_save((0, 0, 10, 10), Image.new("RGB", (10, 10)))
        assert shown.wait(5)
    mock_err.assert_called_once()


def test_process_fullscreen_capture_save_failure_still_copies():
    """全屏保存失败：弹提示但仍复制到剪贴板（不阻断粘贴），返回 None。"""
    import app

    img = Image.new("RGB", (10, 10))
    shown = threading.Event()
    mock_err = MagicMock(side_effect=lambda *a, **k: shown.set())
    with patch.object(app, "save_image", side_effect=OSError("disk full")), \
         patch.object(app, "copy_to_clipboard") as cc, \
         patch.object(app.QMessageBox, "critical", mock_err):
        path = app.process_fullscreen_capture(img)
    assert path is None
    cc.assert_called_once_with(img)
    mock_err.assert_called_once()

