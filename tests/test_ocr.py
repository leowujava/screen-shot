from editor import ocr_result_to_text


def _box(x, y):
    # 4 个角点（左上、右上、右下、左下）
    return [[x, y], [x + 50, y], [x + 50, y + 20], [x, y + 20]]


def test_empty_result():
    assert ocr_result_to_text(None) == ""
    assert ocr_result_to_text([]) == ""


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
