import threading
import time
from unittest.mock import MagicMock, patch

from PIL import Image

from src.app import process_fullscreen_capture


def test_process_fullscreen_saves_image():
    img = MagicMock()
    with patch("src.app.save_image") as si, patch("src.app.copy_to_clipboard") as cc:
        si.return_value = "C:\\shot.png"
        path = process_fullscreen_capture(img)
        assert path == "C:\\shot.png"
        si.assert_called_once_with(img, None)
        cc.assert_called_once_with(img)


def test_do_copy_capture_uses_safe_copy():
    """复制截图应走 _safe_copy_to_clipboard（失败有提示），不再裸吞异常。"""
    from src import app

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
    from src import app

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
    from src import app

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
    from src import app

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
    """取消（region=None）时复制/保存回调不执行任何动作（也不弹保存对话框）。"""
    from src import app

    with patch.object(app, "_safe_copy_to_clipboard") as mock_copy, \
         patch.object(app, "save_image_as") as mock_save, \
         patch.object(app, "_ask_save_path") as mock_ask:
        app._on_copy(None, None)
        app._on_save(None, None)
        mock_copy.assert_not_called()
        mock_save.assert_not_called()
        mock_ask.assert_not_called()


def test_on_save_runs_in_worker_thread():
    """选区保存："另存为"对话框在主线程，PNG 编码在工作线程（不卡主线程事件循环）。"""
    from src import app

    done = threading.Event()
    info = {}
    dialog_threads = []

    def fake_ask():
        dialog_threads.append(threading.get_ident())
        return "C:\\chosen\\shot.png"

    def fake(img, path):
        info["t"] = threading.get_ident()
        info["path"] = path
        done.set()
        return path

    with patch.object(app, "_ask_save_path", fake_ask), \
         patch.object(app, "save_image_as", fake):
        main_id = threading.get_ident()
        app._on_save((0, 0, 10, 10), Image.new("RGB", (10, 10)))
        assert done.wait(5)
    assert dialog_threads == [main_id], "保存对话框应在主线程弹出"
    assert info["t"] != main_id, "save_image_as 不应在主线程执行"
    assert info["path"] == "C:\\chosen\\shot.png"


def test_on_save_cancel_skips_save():
    """"另存为"对话框取消：不保存、不弹错误。"""
    from src import app

    with patch.object(app, "_ask_save_path", return_value=None), \
         patch.object(app, "save_image_as") as mock_save:
        app._on_save((0, 0, 10, 10), Image.new("RGB", (10, 10)))
    mock_save.assert_not_called()


def test_on_save_reports_failure_without_raising():
    """选区保存失败：弹提示而非抛异常（异常不再沿 _exit→_finish 传播到
    Qt 事件循环导致整个应用崩溃）。"""
    from src import app

    shown = threading.Event()
    mock_err = MagicMock(side_effect=lambda *a, **k: shown.set())
    with patch.object(app, "_ask_save_path", return_value="C:\\chosen\\shot.png"), \
         patch.object(app, "save_image_as", side_effect=OSError("disk full")), \
         patch.object(app.QMessageBox, "critical", mock_err):
        app._on_save((0, 0, 10, 10), Image.new("RGB", (10, 10)))
        assert shown.wait(5)
    mock_err.assert_called_once()


def test_process_fullscreen_capture_save_failure_still_copies():
    """全屏保存失败：弹提示但仍复制到剪贴板（不阻断粘贴），返回 None。"""
    from src import app

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


def test_process_fullscreen_saves_to_chosen_path():
    """用户选定路径：写确切路径（save_image_as），不走自动命名。"""
    img = MagicMock()
    with patch("src.app.save_image_as") as sa, patch("src.app.save_image") as si, \
         patch("src.app.copy_to_clipboard") as cc:
        sa.return_value = "C:\\chosen.png"
        path = process_fullscreen_capture(img, "C:\\chosen.png")
        assert path == "C:\\chosen.png"
        sa.assert_called_once_with(img, "C:\\chosen.png")
        si.assert_not_called()
        cc.assert_called_once_with(img)


def test_ask_save_path_defaults_and_remembers_dir(qt_app, monkeypatch):
    """对话框默认路径 = default_save_path；选中后记住目录，下次默认打开该目录。"""
    from src import app

    monkeypatch.setattr(app, "_last_save_dir", None)
    monkeypatch.setattr(app, "default_save_path", lambda: "D:\\shots\\截图_t.png")
    with patch.object(app.QFileDialog, "getSaveFileName",
                      return_value=("D:\\shots\\a.png", "")) as dlg:
        assert app._ask_save_path() == "D:\\shots\\a.png"
    assert dlg.call_args.args[2] == "D:\\shots\\截图_t.png"
    # 第二次：默认目录 = 上次选中目录；取消返回 None
    with patch.object(app.QFileDialog, "getSaveFileName",
                      return_value=("", "")) as dlg:
        assert app._ask_save_path() is None
    assert dlg.call_args.args[2] == "D:\\shots\\截图_t.png"
    assert app._last_save_dir == "D:\\shots"


def test_ask_save_path_without_qapp_returns_default(monkeypatch):
    """无 QApplication（纯逻辑单测）：不弹对话框，直接返回默认路径。"""
    from src import app

    monkeypatch.setattr(app, "_last_save_dir", None)
    monkeypatch.setattr(app, "default_save_path", lambda: "D:\\shots\\x.png")
    with patch.object(app.QApplication, "instance", return_value=None), \
         patch.object(app.QFileDialog, "getSaveFileName") as dlg:
        assert app._ask_save_path() == "D:\\shots\\x.png"
    dlg.assert_not_called()


def test_do_fullscreen_capture_saves_to_chosen_path():
    """全屏截图：截屏（工作线程）→ 选路径 → 保存到选定路径 + 复制。"""
    from src import app

    img = Image.new("RGB", (10, 10))
    done = threading.Event()
    with patch.object(app, "capture_fullscreen", return_value=img), \
         patch.object(app, "_ask_save_path", return_value="C:\\chosen\\full.png") as ask, \
         patch.object(app, "save_image_as", return_value="C:\\chosen\\full.png") as sv, \
         patch.object(app, "copy_to_clipboard", side_effect=lambda im: done.set()) as cc:
        app.do_fullscreen_capture()
        assert done.wait(5)
    ask.assert_called_once()
    sv.assert_called_once_with(img, "C:\\chosen\\full.png")
    cc.assert_called_once_with(img)


def test_do_fullscreen_capture_cancel_still_copies():
    """取消保存对话框：不保存，但仍复制到剪贴板（可 Ctrl+V）。"""
    from src import app

    img = Image.new("RGB", (10, 10))
    done = threading.Event()
    with patch.object(app, "capture_fullscreen", return_value=img), \
         patch.object(app, "_ask_save_path", return_value=None), \
         patch.object(app, "save_image_as") as sv, \
         patch.object(app, "save_image") as sv_auto, \
         patch.object(app, "copy_to_clipboard", side_effect=lambda im: done.set()) as cc:
        app.do_fullscreen_capture()
        assert done.wait(5)
    sv.assert_not_called()
    sv_auto.assert_not_called()
    cc.assert_called_once_with(img)


def test_do_fullscreen_capture_dialog_on_main_thread(qt_app):
    """有 Qt 事件循环时：截屏在工作线程，对话框调度回主线程弹出。"""
    from src import app

    img = Image.new("RGB", (10, 10))
    dialog_threads = []
    done = threading.Event()

    def fake_ask():
        dialog_threads.append(threading.get_ident())
        return "C:\\chosen\\full.png"

    old = app._gui.get("dispatcher")
    app._gui["dispatcher"] = app._GuiDispatcher()
    try:
        with patch.object(app, "capture_fullscreen", return_value=img), \
             patch.object(app, "_ask_save_path", fake_ask), \
             patch.object(app, "save_image_as", return_value="C:\\chosen\\full.png"), \
             patch.object(app, "copy_to_clipboard", side_effect=lambda im: done.set()):
            app.do_fullscreen_capture()
            deadline = time.time() + 5
            while not done.is_set() and time.time() < deadline:
                qt_app.processEvents()
                time.sleep(0.01)
    finally:
        if old is None:
            app._gui.pop("dispatcher", None)
        else:
            app._gui["dispatcher"] = old
    assert done.is_set(), "保存/复制未在工作线程完成"
    assert dialog_threads == [threading.get_ident()], "对话框应调度回主线程"


def test_single_instance_mutex_blocks_second_acquire():
    """单实例：持有句柄期间再次获取返回 None；释放后可重新获取。"""
    import ctypes
    from src import app
    name = "Local\\screen-shot-test-mutex"
    h = app._acquire_single_instance_mutex(name)
    assert h is not None
    try:
        assert app._acquire_single_instance_mutex(name) is None
    finally:
        ctypes.windll.kernel32.CloseHandle(h)
    h2 = app._acquire_single_instance_mutex(name)
    assert h2 is not None
    ctypes.windll.kernel32.CloseHandle(h2)

