from unittest.mock import MagicMock, patch

from app import process_fullscreen_capture, process_region_selection


def test_process_fullscreen_saves_image():
    img = MagicMock()
    with patch("app.save_image") as si, patch("app.copy_to_clipboard") as cc:
        si.return_value = "C:\\shot.png"
        path = process_fullscreen_capture(img)
        assert path == "C:\\shot.png"
        si.assert_called_once_with(img, None)
        cc.assert_called_once_with(img)


def test_process_region_with_selection_copies_and_opens_editor():
    img = MagicMock()
    with patch("app.capture_region") as cr, patch("app.copy_to_clipboard") as cc, \
            patch("app.Editor") as ed:
        cr.return_value = MagicMock()
        ed.return_value.show = MagicMock()
        process_region_selection(img, (0, 0, 100, 100))
        cr.assert_called_once_with(0, 0, 100, 100)
        cc.assert_called_once()
        ed.assert_called_once()


def test_process_region_no_selection_ignores():
    img = MagicMock()
    with patch("app.Editor") as ed:
        ed.return_value.show = MagicMock()
        process_region_selection(img, None)
        ed.assert_not_called()
