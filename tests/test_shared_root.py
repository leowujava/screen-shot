"""验证"共享根窗口"方案下，选区结束后打开编辑器不会触发
TclError: image doesn't exist（原 bug 场景）。

在单个 Tcl 解释器内：共享根 → 选区 Toplevel → 选区结束 → 编辑器 Toplevel +
PhotoImage，全程只销毁 Toplevel、不销毁解释器，应无异常。
"""
import tkinter as tk
from PIL import Image, ImageTk

from selector import RegionSelector


def test_shared_root_flow_no_tclerror():
    results = {}

    def on_select(region):
        assert region is None  # 走取消路径（Esc），不依赖真实拖拽
        # 模拟 app.py：在共享根上打开编辑器
        ed = tk.Toplevel(shared)
        ed.title("截图编辑")
        canvas = tk.Canvas(ed, width=120, height=90, highlightthickness=0)
        canvas.pack()
        photo = ImageTk.PhotoImage(Image.new("RGB", (120, 90), "red"))
        canvas.create_image(10, 10, anchor="nw", image=photo)
        results["photo"] = photo
        ed.after(50, ed.destroy)

    shared = tk.Tk()
    shared.withdraw()

    sel = RegionSelector(x=0, y=0, width=400, height=300,
                         on_select=on_select, root=shared)
    # 非阻塞：show() 应直接返回
    sel.show()

    # 用 after 触发"取消"，随后结束主循环
    shared.after(30, lambda: sel._cancel())
    shared.after(200, shared.quit)

    shared.mainloop()

    assert "photo" in results, "编辑器未打开（on_select 未触发）"
    # 关键：共享根仍存活，可继续驱动窗口（解释器未被破坏）
    assert shared.winfo_exists()
