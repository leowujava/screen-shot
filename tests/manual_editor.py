import sys

from PIL import Image

from PySide6.QtWidgets import QApplication

from editor import EditorWindow

if __name__ == "__main__":
    app = QApplication(sys.argv)
    img = Image.new("RGB", (800, 500), "lightblue")
    ed = EditorWindow(img)
    ed.show()
    app.exec()
