"""验证 process_region_selection 直接打开 Editor（不再走 AnnotationWindow）。"""
import pytest
from unittest.mock import MagicMock, patch
from PIL import Image


@pytest.fixture
def mock_editor():
    with patch("app.Editor") as mock_ed:
        mock_ed.return_value = MagicMock()
        yield mock_ed


def test_process_region_selection_opens_editor(mock_editor):
    import app
    app._tk_root_holder["root"] = MagicMock()

    full = Image.new("RGB", (100, 100), (255, 255, 255))
    region = (10, 10, 50, 50)
    annotated = full.crop((10, 10, 60, 60))

    app.process_region_selection(region, annotated)

    mock_editor.assert_called_once()
    call_args = mock_editor.call_args
    assert call_args[0][0] is annotated  # image
    assert call_args[1].get("root") is app._tk_root_holder["root"]  # root kwarg


def test_process_region_selection_none_region_does_nothing(mock_editor):
    import app
    app._tk_root_holder["root"] = MagicMock()

    app.process_region_selection(None, None)

    mock_editor.assert_not_called()
