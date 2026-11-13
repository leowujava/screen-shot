"""模块拆分回归：selector 保持门面接口，子模块可独立导入。

selector.py 拆出 theme（常量）/ tb_icons（图标）/ widgets（辅助窗口）后，
`import selector` 的既有名字必须仍然可用且指向同一对象
（测试与手动脚本直接引用 selector._tb_icon 等）。
"""
import selector
import tb_icons
import theme
import widgets


def test_selector_reexports_are_same_objects():
    assert selector._tb_icon is tb_icons._tb_icon
    assert selector.TOOL_ICON_FNS is tb_icons.TOOL_ICON_FNS
    assert selector._Toolbar is widgets._Toolbar
    assert selector._OcrResultDialog is widgets._OcrResultDialog
    assert selector.PALETTE is theme.PALETTE
    assert selector.BORDER_INNER is theme.BORDER_INNER


def test_all_tool_icons_reexported():
    """14 个图标绘制函数全部可从 selector 访问（测试直接引用）。"""
    for name in ("move", "brush", "rect", "ellipse", "arrow", "mosaic",
                 "text", "undo", "copy", "ocr", "color", "save",
                 "confirm", "cancel"):
        fn = getattr(selector, f"_paint_icon_{name}")
        assert fn is getattr(tb_icons, f"_paint_icon_{name}")


def test_submodules_import_without_selector():
    """theme / tb_icons / widgets 不反向依赖 selector（无循环导入）。"""
    import subprocess
    import sys
    code = "import theme, tb_icons, widgets"
    r = subprocess.run([sys.executable, "-c", code],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
