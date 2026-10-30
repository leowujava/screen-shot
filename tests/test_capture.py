from PIL import Image
from capture import capture_fullscreen, capture_region, capture_virtual_screen_size


def test_capture_fullscreen_returns_image():
    img = capture_fullscreen()
    assert isinstance(img, Image.Image)
    assert img.width > 0 and img.height > 0


def test_capture_fullscreen_multiple_monitors():
    img = capture_fullscreen()
    assert img.size == capture_virtual_screen_size()


def test_capture_region():
    full = capture_fullscreen()
    w, h = full.size
    region = capture_region(0, 0, w // 2, h // 2)
    assert isinstance(region, Image.Image)
    assert region.size == (w // 2, h // 2)
