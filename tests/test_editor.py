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
