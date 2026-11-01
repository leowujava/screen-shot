from unittest.mock import MagicMock, patch

from app import process_fullscreen_capture


def test_process_fullscreen_saves_image():
    img = MagicMock()
    with patch("app.save_image") as si, patch("app.copy_to_clipboard") as cc:
        si.return_value = "C:\\shot.png"
        path = process_fullscreen_capture(img)
        assert path == "C:\\shot.png"
        si.assert_called_once_with(img, None)
        cc.assert_called_once_with(img)

