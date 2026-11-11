"""验证"选区结束 → 打开编辑器"的完整流程不会破坏 Qt 事件循环。

PySide6 版下不存在 Tk 的共享 Tcl 解释器问题，等价回归是：
选区窗口 hide 结束后，同一 QApplication 事件循环仍能正常驱动新窗口
（编辑器），窗口互不干扰。
"""
from PIL import Image

from editor import EditorWindow
from selector import Selector


def test_selector_close_then_editor_opens(qt_app):
    results = {}

    def on_select(region, annotated_img):
        assert region is None  # 走取消路径（Esc），不依赖真实拖拽
        # 模拟 app 流程：选区结束后打开编辑器
        ed = EditorWindow(Image.new("RGB", (120, 90), "red"))
        ed.show()
        qt_app.processEvents()
        results["editor"] = ed

    sel = Selector(x=0, y=0, width=400, height=300,
                   full_img=None,
                   on_select=on_select,
                   on_copy=lambda r, i: None,
                   on_save=lambda r, i: None)
    # 非阻塞：show() 应直接返回
    sel.show()
    qt_app.processEvents()

    sel._cancel()
    qt_app.processEvents()

    assert "editor" in results, "编辑器未打开（on_select 未触发）"
    # 关键：事件循环仍存活，编辑器窗口可正常驱动（标题/预览正常）
    ed = results["editor"]
    assert ed.windowTitle() == "screen-shot 编辑器"
    assert ed.label.pixmap() is not None
    ed.close()
