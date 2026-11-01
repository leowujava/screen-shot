"""验证 process_region_selection 打开 AnnotationWindow 而不是直接打开 Editor。"""
import pytest
from unittest.mock import MagicMock, patch
from PIL import Image


@pytest.fixture
def mock_annotation():
    with patch("app.AnnotationWindow") as mock_aw:
        mock_aw.return_value = MagicMock()
        yield mock_aw


def test_process_region_selection_opens_annotation(mock_annotation):
    import app
    app._tk_root_holder["root"] = MagicMock()

    full = Image.new("RGB", (100, 100), (255, 255, 255))
    region = (10, 10, 50, 50)

    app.process_region_selection(full, region)

    # AnnotationWindow 应该被调用
    mock_annotation.assert_called_once()
    # 检查参数
    call_args = mock_annotation.call_args
    assert call_args[0][0] is full  # full_img
    assert call_args[0][1] == region  # region


def test_process_region_selection_none_region_does_nothing(mock_annotation):
    import app
    app._tk_root_holder["root"] = MagicMock()

    full = Image.new("RGB", (100, 100), (255, 255, 255))
    app.process_region_selection(full, None)

    # AnnotationWindow 不应该被调用
    mock_annotation.assert_not_called()
