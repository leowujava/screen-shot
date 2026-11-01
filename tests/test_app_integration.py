"""验证 process_region_selection 确认后只复制到剪贴板，不自动保存、不打开 Editor。"""
import pytest
from unittest.mock import MagicMock, patch
from PIL import Image


def test_process_region_selection_copies_only():
    """确认后应只复制到剪贴板，不自动保存。"""
    import app
    app._tk_root_holder["root"] = MagicMock()

    full = Image.new("RGB", (100, 100), (255, 255, 255))
    region = (10, 10, 50, 50)
    annotated = full.crop((10, 10, 60, 60))

    with patch.object(app, "_safe_copy_to_clipboard") as mock_copy, \
         patch.object(app, "save_image") as mock_save:
        app.process_region_selection(region, annotated)
        mock_copy.assert_called_once_with(annotated)
        mock_save.assert_not_called()


def test_process_region_selection_none_region_does_nothing():
    """region 为 None 时不做任何事。"""
    import app
    app._tk_root_holder["root"] = MagicMock()

    with patch.object(app, "_safe_copy_to_clipboard") as mock_copy, \
         patch.object(app, "save_image") as mock_save:
        app.process_region_selection(None, None)
        mock_copy.assert_not_called()
        mock_save.assert_not_called()
