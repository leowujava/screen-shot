from PIL import Image

from editor import EditorWindow


def test_editor_window_smoke(qt_app):
    """offscreen 下可创建编辑器窗口并渲染预览。"""
    img = Image.new("RGBA", (300, 200), (10, 20, 30, 255))
    ed = EditorWindow(img)
    ed.show()
    qt_app.processEvents()
    assert ed.windowTitle() == "screen-shot 编辑器"
    # 预览已渲染
    assert ed.label.pixmap() is not None
    # 构造时复制了图像（编辑器内部修改不影响原图）
    assert ed.image is not img
    ed.close()


def test_pil_to_qimage(qt_app):
    from PySide6.QtGui import QImage
    img = Image.new("RGB", (4, 3))
    qimg = EditorWindow._pil_to_qimage(img)
    assert qimg.width() == 4
    assert qimg.height() == 3
    assert qimg.format() == QImage.Format.Format_RGBA8888


def test_ocr_reexported_from_drawing():
    """editor 的 OCR 逻辑必须来自 drawing 的唯一实现（去重）。"""
    import editor, drawing
    assert editor.ocr_result_to_text is drawing.ocr_result_to_text


def test_editor_ocr_runs_in_worker_thread(qt_app):
    """OCR 在工作线程执行，结果通过信号投递回主线程弹窗（不阻塞界面）。

    回归：旧实现直接在 GUI 线程同步跑引擎，OCR 期间窗口完全卡死。
    """
    import threading
    import time
    from unittest.mock import MagicMock, patch
    import editor

    eng_thread = []

    def fake_engine(arr):
        eng_thread.append(threading.current_thread())
        return [[[[0, 0], [50, 0], [50, 10], [0, 10]], "hello", 0.99]]

    ed = EditorWindow(Image.new("RGB", (60, 30), (255, 255, 255)))
    mock_info = MagicMock()
    with patch.object(editor, "get_ocr_engine", return_value=fake_engine), \
         patch.object(editor.QMessageBox, "information", mock_info):
        ed.ocr()
        deadline = time.time() + 5
        while not mock_info.called and time.time() < deadline:
            qt_app.processEvents()
            time.sleep(0.02)
    try:
        assert eng_thread, "OCR 引擎未被调用"
        assert eng_thread[0] is not threading.main_thread(), "OCR 未在工作线程执行"
        assert mock_info.called, "OCR 结果未投递回主线程弹窗"
        assert "hello" in str(mock_info.call_args)
    finally:
        ed.close()
