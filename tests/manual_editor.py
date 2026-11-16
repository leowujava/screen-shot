import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image

from PySide6.QtWidgets import QApplication

from src.tools import EditorWindow

if __name__ == "__main__":
    app = QApplication(sys.argv)
    img = Image.new("RGB", (800, 500), "lightblue")
    ed = EditorWindow(img)
    ed.show()
    app.exec()
