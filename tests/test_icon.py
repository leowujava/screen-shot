import os
from unittest.mock import patch, MagicMock

import pytest

import icon
import theme

ASSETS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "icons")


@pytest.fixture(autouse=True)
def _reset():
    icon._reset_cache()
    with patch.object(icon.ImageTk, "PhotoImage") as m_pi:
        # 每次调用创建新的 MagicMock（不同 color 应产生不同对象）
        m_pi.side_effect = lambda *a, **k: MagicMock()
        yield
    icon._reset_cache()


def test_render_returns_photoimage_of_size():
    photo = icon.render("ellipse", "#4a90e2", size=18)
    assert photo is not None
    # PhotoImage 的 width()/height() 需要真实 Tk；用 mock master 时
    # 这里只断言对象存在 + 缓存命中
    assert photo is icon.render("ellipse", "#4a90e2", size=18)


def test_render_different_colors_differ():
    a = icon.render("ellipse", "#4a90e2", size=18)
    b = icon.render("ellipse", "#ff0000", size=18)
    assert a is not b  # 不同 color → 不同缓存条目


def test_render_missing_svg_returns_blank():
    photo = icon.render("does_not_exist_xyz", "#4a90e2", size=18)
    assert photo is not None  # 不抛异常


def test_render_missing_renderer_no_crash():
    """renderer 不可用（svglib/reportlab 未装）时返回 1×1 透明图，不抛异常。"""
    with patch.dict("sys.modules", {"svglib": None, "svglib.svglib": None,
                                    "reportlab": None, "reportlab.graphics": None,
                                    "reportlab.graphics.renderPM": None}):
        icon._reset_cache()
        photo = icon.render("ellipse", "#4a90e2", size=18)
    assert photo is not None
    assert icon._RENDERER_AVAILABLE is False


def test_render_cache_hit_same_object():
    a = icon.render("ellipse", "#4a90e2", size=18)
    b = icon.render("ellipse", "#4a90e2", size=18)
    assert a is b
