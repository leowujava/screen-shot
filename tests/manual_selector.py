import sys

from PySide6.QtWidgets import QApplication

from selector import RegionSelector
from capture import capture_fullscreen


def on_select(region, annotated_img):
    print("Selected:", region)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    sel = RegionSelector(
        x=0, y=0, width=1920, height=1080,
        full_img=None,
        on_select=on_select,
        on_copy=lambda r, i: print("Copy:", r),
        on_save=lambda r, i: print("Save:", r),
        capture_screen=capture_fullscreen,
    )
    sel.show()
    app.exec()
