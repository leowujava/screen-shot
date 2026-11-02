import re
import theme


def _is_hex(c):
    return isinstance(c, str) and re.fullmatch(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})", c) is not None


def test_theme_light_keys():
    required = {"surface", "border", "fg", "hover", "hover_fg",
                "active", "active_fg", "disabled", "sep"}
    assert required <= set(theme.LIGHT), f"LIGHT 缺少键: {required - set(theme.LIGHT)}"


def test_theme_dark_keys():
    required = {"surface", "border", "fg", "hover", "hover_fg", "btn", "btn_fg",
                "btn_hover", "active", "active_fg", "primary", "primary_hover",
                "sep", "confirm", "cancel"}
    assert required <= set(theme.DARK), f"DARK 缺少键: {required - set(theme.DARK)}"


def test_theme_hex_colors():
    for name, palette in (("LIGHT", theme.LIGHT), ("DARK", theme.DARK)):
        for k, v in palette.items():
            assert _is_hex(v), f"{name}.{k} = {v!r} 不是合法 hex 颜色"
    assert _is_hex(theme.ACCENT)
    for c in theme.PALETTE:
        assert _is_hex(c), f"PALETTE 含非法 hex: {c!r}"


def test_theme_palette_length():
    assert len(theme.PALETTE) == 10


def test_theme_sizes():
    assert theme.ICON_SIZE == 18
    assert theme.TB_RADIUS == 8
    assert theme.BTN_RADIUS == 6
    assert theme.BTN_PAD_X == 8
    assert theme.BTN_PAD_Y == 6
    assert theme.SEP_W == 1
    assert theme.SEP_H == 18
