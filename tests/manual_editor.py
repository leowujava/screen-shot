from PIL import Image

from editor import Editor

if __name__ == "__main__":
    img = Image.new("RGB", (800, 500), "lightblue")
    Editor(img, on_close=lambda: print("closed")).show()
