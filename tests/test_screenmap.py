"""screenmap 坐标映射测试：单屏高 DPI 退化 + 多屏混合 DPI 精确映射。"""
import screenmap


def test_fallback_uniform_scale():
    """无真实显示器信息时退化为单屏均匀缩放（单屏 150% 场景）。"""
    mappings, exact = screenmap.build_mappings(100, 100, 150, 150)
    assert exact is False
    assert len(mappings) == 1
    m = mappings[0]
    assert (m.lx, m.ly, m.lw, m.lh) == (0, 0, 100, 100)
    assert (m.px, m.py, m.pw, m.ph) == (0, 0, 150, 150)
    assert m.sx == 1.5 and m.sy == 1.5
    assert m.local_to_physical(40, 60) == (60, 90)


def test_exact_mixed_dpi_mapping(monkeypatch):
    """模拟 100% + 200% 双屏：按 Qt 逻辑几何 × DPR 匹配物理显示器。

    Qt 在混合 DPI 下逐屏换算，逻辑屏幕之间可能出现重叠（第二块屏
    logical x = 960，物理 x = 1920）。映射必须按显示器各自比例换算。
    """
    # (vx, vy, vw, vh), [((x, y, w, h), dpr), ...]
    monkeypatch.setattr(screenmap, "_qt_screens", lambda: (
        (0, 0, 2880, 1080),
        [((0, 0, 1920, 1080), 1.0),
         ((960, 0, 1920, 1080), 2.0)],
    ))
    monkeypatch.setattr(screenmap, "_win32_monitors", lambda: [
        (0, 0, 1920, 1080),
        (1920, 0, 5760, 2160),
    ])

    mappings, exact = screenmap.build_mappings(2880, 1080, 5760, 2160)
    assert exact is True
    assert len(mappings) == 2

    first, second = mappings
    assert (first.lx, first.ly, first.lw, first.lh) == (0, 0, 1920, 1080)
    assert (first.px, first.py, first.pw, first.ph) == (0, 0, 1920, 1080)
    assert first.sx == 1.0

    assert (second.lx, second.ly, second.lw, second.lh) == (960, 0, 1920, 1080)
    assert (second.px, second.py, second.pw, second.ph) == (1920, 0, 3840, 2160)
    assert second.sx == 2.0
    # 第二块屏逻辑 (1000, 100) → 物理 (1920 + 40*2, 100*2)
    assert second.local_to_physical(1000, 100) == (2000, 200)


def test_size_based_matching_when_logical_positions_differ(monkeypatch):
    """Qt 混合 DPI 下逻辑位置未按 DPR 换算时，仍能按物理尺寸完成匹配。"""
    monkeypatch.setattr(screenmap, "_qt_screens", lambda: (
        (0, 0, 3840, 1080),
        [((0, 0, 1920, 1080), 1.0),
         ((1920, 0, 1920, 1080), 2.0)],
    ))
    monkeypatch.setattr(screenmap, "_win32_monitors", lambda: [
        (0, 0, 1920, 1080),
        (1920, 0, 5760, 2160),
    ])

    mappings, exact = screenmap.build_mappings(3840, 1080, 5760, 2160)
    assert exact is True
    first, second = mappings
    assert (first.px, first.pw, first.sx) == (0, 1920, 1.0)
    assert (second.px, second.pw, second.sx) == (1920, 3840, 2.0)


def test_mismatched_physical_size_falls_back(monkeypatch):
    """显示器信息与实际截图尺寸不一致时退回退化映射，不产生错位映射。"""
    monkeypatch.setattr(screenmap, "_qt_screens", lambda: (
        (0, 0, 100, 100), [((0, 0, 100, 100), 1.0)]))
    monkeypatch.setattr(screenmap, "_win32_monitors", lambda: [
        (0, 0, 999, 999)])

    mappings, exact = screenmap.build_mappings(100, 100, 150, 150)
    assert exact is False
    assert mappings[0].pw == 150
