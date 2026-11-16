"""薄入口：兼容 `python app.py` 与 PyInstaller（screen-shot.spec 以本文件为入口）。

实际逻辑在 src 包内（src/app.py），等价于 `python -m src`。
"""
from src.app import main

if __name__ == "__main__":
    main()
