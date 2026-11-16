"""模块拆分回归：selector 保持门面接口，子模块可独立导入。

selector.py 拆出 theme（常量）/ tb_icons（图标）/ widgets（辅助窗口）/
qtutil（Qt 工具）/ selector_toolbar / selector_mouse / selector_region（Mixin）后，
`import selector` 的既有名字必须仍然可用且指向同一对象
（测试与手动脚本直接引用 selector._tb_icon 等）。
"""
import selector
import tb_icons
import theme
import widgets
import qtutil
import selector_region
import selector_toolbar
import selector_mouse


def test_selector_reexports_are_same_objects():
    assert selector._tb_icon is tb_icons._tb_icon
    assert selector.TOOL_ICON_FNS is tb_icons.TOOL_ICON_FNS
    assert selector._Toolbar is widgets._Toolbar
    assert selector._OcrResultDialog is widgets._OcrResultDialog
    assert selector.PALETTE is theme.PALETTE
    assert selector.BORDER_INNER is theme.BORDER_INNER
    assert selector._pil_to_qpixmap is qtutil._pil_to_qpixmap
    assert selector._virtual_screen_size is qtutil._virtual_screen_size
    assert selector._event_pos is qtutil._event_pos
    assert selector._UNDO_MAXLEN == selector_region._UNDO_MAXLEN
    assert selector._UNDO_MEMORY_BUDGET == selector_region._UNDO_MEMORY_BUDGET


def test_all_tool_icons_reexported():
    """14 个图标绘制函数全部可从 selector 访问（测试直接引用）。"""
    for name in ("move", "brush", "rect", "ellipse", "arrow", "mosaic",
                 "text", "undo", "copy", "ocr", "color", "save",
                 "confirm", "cancel"):
        fn = getattr(selector, f"_paint_icon_{name}")
        assert fn is getattr(tb_icons, f"_paint_icon_{name}")


def test_mixins_composed_into_selector():
    """三个 Mixin 都在 Selector 的 MRO 中，且关键方法来自预期模块。"""
    assert selector_toolbar.ToolbarMixin in selector.Selector.__mro__
    assert selector_mouse.MouseMixin in selector.Selector.__mro__
    assert selector_region.RegionMixin in selector.Selector.__mro__
    assert selector.Selector.mousePressEvent is selector_mouse.MouseMixin.mousePressEvent
    assert selector.Selector._build_toolbar is selector_toolbar.ToolbarMixin._build_toolbar
    assert selector.Selector._crop_region is selector_region.RegionMixin._crop_region
    # 渲染/兜底留在 selector 本体（测试 monkeypatch selector.QMessageBox /
    # selector._pil_to_qpixmap 依赖这些调用点的模块归属）
    assert "paintEvent" in selector.Selector.__dict__
    assert "_abort_capture" in selector.Selector.__dict__
    assert "_refresh_region_pixmap" in selector.Selector.__dict__


def test_submodules_import_without_selector():
    """子模块不反向依赖 selector（无循环导入）。"""
    import subprocess
    import sys
    code = ("import theme, tb_icons, widgets, qtutil, "
            "selector_region, selector_toolbar, selector_mouse")
    r = subprocess.run([sys.executable, "-c", code],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
