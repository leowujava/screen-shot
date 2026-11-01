import tkinter as tk
from unittest.mock import MagicMock, call
import pytest
from PIL import Image
from annotation import AnnotationWindow


@pytest.fixture
def fake_root():
    """让 AnnotationWindow 使用 mock 的 Tk 窗口，不创建真实 GUI。"""
    import sys
    import types

    with pytest.MonkeyPatch.context() as m:
        mock_root = MagicMock(name="tk_root")
        mock_root.geometry = MagicMock()
        mock_root.winfo_id = MagicMock(return_value=12345)
        m.setattr(tk, "Tk", lambda: mock_root)
        m.setattr(tk, "Toplevel", MagicMock(return_value=mock_root))
        m.setattr(tk, "Canvas", MagicMock(return_value=MagicMock(name="canvas")))
        m.setattr(tk, "Frame", MagicMock(return_value=MagicMock(name="frame")))
        m.setattr(tk, "Label", MagicMock(return_value=MagicMock(name="label")))
        # mock ImageTk
        fake_pil = types.ModuleType("PIL")
        fake_pil_image = types.ModuleType("PIL.Image")
        fake_pil_imagetk = types.ModuleType("PIL.ImageTk")
        fake_pil_imagetk.PhotoImage = MagicMock(name="PhotoImage")
        fake_pil.Image = fake_pil_image
        fake_pil.ImageTk = fake_pil_imagetk
        m.setitem(sys.modules, "PIL", fake_pil)
        m.setitem(sys.modules, "PIL.Image", fake_pil_image)
        m.setitem(sys.modules, "PIL.ImageTk", fake_pil_imagetk)
        yield mock_root


def test_annotation_window_init_crops_region(fake_root):
    full = Image.new("RGB", (100, 100), (255, 255, 255))
    # 画一个红块在 (20, 30, 60, 70)
    from PIL import ImageDraw
    d = ImageDraw.Draw(full)
    d.rectangle([20, 30, 60, 70], fill=(255, 0, 0))

    win = AnnotationWindow(
        full_img=full,
        region=(20, 30, 40, 40),
        on_confirm=lambda img: None,
        on_copy=lambda img: None,
        on_save=lambda img: None,
        on_cancel=lambda: None,
        root=MagicMock(),
    )
    # region_img 应该是 full 的 (20, 30, 60, 70) crop
    assert win.region_img.size == (40, 40)
    # crop 中心应为红色
    assert win.region_img.getpixel((20, 20)) == (255, 0, 0)


def test_annotation_window_confirm_passes_region_img(fake_root):
    full = Image.new("RGB", (100, 100), (255, 255, 255))
    confirmed = {}

    win = AnnotationWindow(
        full_img=full,
        region=(10, 10, 50, 50),
        on_confirm=lambda img: confirmed.update({"img": img}),
        on_copy=lambda img: None,
        on_save=lambda img: None,
        on_cancel=lambda: None,
        root=MagicMock(),
    )
    win._on_confirm()
    assert "img" in confirmed
    assert confirmed["img"].size == (50, 50)


def test_annotation_window_copy_passes_region_img(fake_root):
    full = Image.new("RGB", (100, 100), (0, 0, 0))
    copied = {}

    win = AnnotationWindow(
        full_img=full,
        region=(10, 10, 30, 30),
        on_confirm=lambda img: None,
        on_copy=lambda img: copied.update({"img": img}),
        on_save=lambda img: None,
        on_cancel=lambda: None,
        root=MagicMock(),
    )
    win._on_copy()
    assert "img" in copied
    assert copied["img"].size == (30, 30)


def test_annotation_window_cancel_calls_on_cancel(fake_root):
    full = Image.new("RGB", (100, 100), (0, 0, 0))
    cancelled = {}

    win = AnnotationWindow(
        full_img=full,
        region=(10, 10, 30, 30),
        on_confirm=lambda img: None,
        on_copy=lambda img: None,
        on_save=lambda img: None,
        on_cancel=lambda: cancelled.update({"called": True}),
        root=MagicMock(),
    )
    win._on_cancel()
    assert cancelled.get("called") is True


def test_adjust_region_clears_annotations(fake_root):
    full = Image.new("RGB", (100, 100), (255, 255, 255))
    from PIL import ImageDraw
    d = ImageDraw.Draw(full)
    d.rectangle([20, 20, 60, 60], fill=(255, 0, 0))

    win = AnnotationWindow(
        full_img=full,
        region=(20, 20, 40, 40),
        on_confirm=lambda img: None,
        on_copy=lambda img: None,
        on_save=lambda img: None,
        on_cancel=lambda: None,
        root=MagicMock(),
    )
    # 在 region_img 上画一条线（模拟标注）
    d2 = ImageDraw.Draw(win.region_img)
    d2.line([(0, 0), (40, 40)], fill=(0, 0, 255), width=3)

    # 调整选区（模拟手柄拖动）
    win._adjust_region(10, 10, 70, 70)

    # region_img 应该被重新 crop（从 full_img 的 10,10 到 70,70）
    assert win.region_img.size == (60, 60)
    # 之前的标注应该消失（重新 crop 后是原始 full 的内容）
    # 中心 (30, 30) 在 full 中是红色区域，所以 crop 后中心也是红色
    assert win.region_img.getpixel((30, 30)) == (255, 0, 0)
    # 撤销栈应该被清空
    assert win.undo.pop() is None
