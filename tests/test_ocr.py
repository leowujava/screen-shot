from editor import ocr_result_to_text


def _box(x, y):
    # 4 个角点（左上、右上、右下、左下）
    return [[x, y], [x + 50, y], [x + 50, y + 20], [x, y + 20]]


def test_empty_result():
    assert ocr_result_to_text(None) == ""
    assert ocr_result_to_text([]) == ""
    assert ocr_result_to_text(([], [])) == ""


def test_sorts_by_position_reading_order():
    # 故意乱序：右下角、左上、左下、右上
    r = [
        [_box(120, 40), "D", 0.9],
        [_box(10, 40), "A", 0.9],
        [_box(10, 10), "B", 0.9],
        [_box(120, 10), "C", 0.9],
    ]
    # 期望按 行(先上后下) → 列(先左后右) 排序：B C / A D
    assert ocr_result_to_text(r) == "B\nC\nA\nD"


def test_rapidocr_v3_output_format():
    # 模拟 rapidocr (v3) 的 RapidOCROutput：.boxes 与 .txts 一一对应
    class Out:
        pass
    o = Out()
    o.boxes = [_box(10, 10), _box(120, 10), _box(10, 40), _box(120, 40)]
    o.txts = ["B", "C", "A", "D"]
    assert ocr_result_to_text(o) == "B\nC\nA\nD"


def test_rapidocr_v3_empty():
    class Out:
        boxes = []
        txts = ()
    assert ocr_result_to_text(Out()) == ""


def test_single_line():
    r = [[_box(0, 0), "hello", 0.8]]
    assert ocr_result_to_text(r) == "hello"


def test_rapidocr_onnxruntime_new_tuple_format():
    # rapidocr_onnxruntime 新版：( [ [box, text, conf], ... ], [times] )
    r = (
        [
            [_box(120, 40), "D", 0.9],
            [_box(10, 40), "A", 0.9],
            [_box(10, 10), "B", 0.9],
            [_box(120, 10), "C", 0.9],
        ],
        [0.1, 0.2, 0.3],
    )
    assert ocr_result_to_text(r) == "B\nC\nA\nD"


def test_ocr_engine_initialized_once_under_concurrent_calls(monkeypatch):
    """OCR 点击可能并发；模型懒加载必须只执行一次（工作线程安全）。"""
    import sys
    import threading
    import time
    import types

    import drawing

    calls = []
    calls_lock = threading.Lock()

    class FakeRapidOCR:
        def __init__(self):
            with calls_lock:
                calls.append(1)
            time.sleep(0.05)  # 放大并发窗口

    fake = types.ModuleType("rapidocr")
    fake.RapidOCR = FakeRapidOCR
    monkeypatch.setitem(sys.modules, "rapidocr", fake)
    monkeypatch.setattr(drawing, "_OCR_ENGINE", None)
    monkeypatch.setattr(drawing, "_OCR_ENGINE_READY", False)

    barrier = threading.Barrier(8)
    results = []

    def worker():
        barrier.wait()
        results.append(drawing.get_ocr_engine())

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert len(calls) == 1, f"OCR 引擎被初始化 {len(calls)} 次"
    assert all(r is not None for r in results)
