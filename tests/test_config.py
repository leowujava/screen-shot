"""用户配置（config.py）：默认值 / 容错 / 字段校验 / 接入点。"""
import json

import pytest

import config


def _write(tmp_path, data):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(p)


# --------------------------------------------------------------------------- #
# 默认值与容错
# --------------------------------------------------------------------------- #

def test_missing_file_returns_defaults(tmp_path):
    cfg = config.load_config(str(tmp_path / "nonexistent.json"))
    assert cfg == {
        "hotkeys": dict(config.DEFAULT_HOTKEYS),
        "save_dir": "",
        "color": config.DEFAULT_COLOR,
        "width": config.DEFAULT_WIDTH,
    }


def test_corrupt_json_returns_defaults(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("{不是合法 JSON", encoding="utf-8")
    assert config.load_config(str(p))["hotkeys"] == config.DEFAULT_HOTKEYS


def test_non_object_json_returns_defaults(tmp_path):
    p = _write(tmp_path, [1, 2, 3])
    assert config.load_config(p)["width"] == config.DEFAULT_WIDTH


def test_partial_config_keeps_other_defaults(tmp_path):
    p = _write(tmp_path, {"width": 7})
    cfg = config.load_config(p)
    assert cfg["width"] == 7
    assert cfg["color"] == config.DEFAULT_COLOR
    assert cfg["hotkeys"] == config.DEFAULT_HOTKEYS
    assert cfg["save_dir"] == ""


def test_defaults_match_historical_hardcoded_behavior():
    """默认值必须与配置功能引入前的硬编码行为一致。"""
    import theme
    assert config.DEFAULT_COLOR == theme.PALETTE[0]
    assert config.DEFAULT_WIDTH == 3
    assert config.DEFAULT_HOTKEYS == {
        "region": "<ctrl>+<shift>+x",
        "fullscreen": "<ctrl>+<shift>+s",
        "quit": "<ctrl>+<shift>+q",
    }


# --------------------------------------------------------------------------- #
# 字段校验
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("bad", ["3", 3.5, True, None, [3]])
def test_invalid_width_falls_back(tmp_path, bad):
    p = _write(tmp_path, {"width": bad})
    assert config.load_config(p)["width"] == config.DEFAULT_WIDTH


@pytest.mark.parametrize("val,expected", [(0, 1), (99, 30), (1, 1), (30, 30)])
def test_width_clamped_to_spinbox_range(tmp_path, val, expected):
    p = _write(tmp_path, {"width": val})
    assert config.load_config(p)["width"] == expected


@pytest.mark.parametrize("bad", ["red", "#fff", "#12345g", 123, None])
def test_invalid_color_falls_back(tmp_path, bad):
    p = _write(tmp_path, {"color": bad})
    assert config.load_config(p)["color"] == config.DEFAULT_COLOR


def test_valid_color_normalized_to_lowercase(tmp_path):
    p = _write(tmp_path, {"color": " #3370FF "})
    assert config.load_config(p)["color"] == "#3370ff"


def test_save_dir_expanded_and_stripped(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", "C:\\Users\\测试")
    monkeypatch.setenv("HOME", "C:\\Users\\测试")
    p = _write(tmp_path, {"save_dir": " ~\\Pictures\\截图 "})
    assert config.load_config(p)["save_dir"] == "C:\\Users\\测试\\Pictures\\截图"


@pytest.mark.parametrize("bad", [123, None, "   "])
def test_invalid_save_dir_means_desktop(tmp_path, bad):
    p = _write(tmp_path, {"save_dir": bad})
    assert config.load_config(p)["save_dir"] == ""


# --------------------------------------------------------------------------- #
# 热键校验
# --------------------------------------------------------------------------- #

def test_custom_hotkeys(tmp_path):
    p = _write(tmp_path, {"hotkeys": {"region": "<ctrl>+<alt>+a"}})
    cfg = config.load_config(p)
    assert cfg["hotkeys"]["region"] == "<ctrl>+<alt>+a"
    assert cfg["hotkeys"]["fullscreen"] == config.DEFAULT_HOTKEYS["fullscreen"]


@pytest.mark.parametrize("bad", [42, None, ["<ctrl>+x"]])
def test_invalid_hotkey_falls_back(tmp_path, bad):
    p = _write(tmp_path, {"hotkeys": {"region": bad}})
    assert config.load_config(p)["hotkeys"]["region"] == \
        config.DEFAULT_HOTKEYS["region"]


def test_hotkeys_not_dict_falls_back(tmp_path):
    p = _write(tmp_path, {"hotkeys": "<ctrl>+x"})
    assert config.load_config(p)["hotkeys"] == config.DEFAULT_HOTKEYS


def test_duplicate_hotkey_later_action_yields_to_default(tmp_path):
    """region 占用 fullscreen 的默认组合：fullscreen 让位（禁用），结果确定。"""
    p = _write(tmp_path, {"hotkeys": {
        "region": config.DEFAULT_HOTKEYS["fullscreen"],
    }})
    hk = config.load_config(p)["hotkeys"]
    assert hk["region"] == config.DEFAULT_HOTKEYS["fullscreen"]
    assert hk["fullscreen"] == ""   # 默认组合也被占 → 禁用
    assert hk["quit"] == config.DEFAULT_HOTKEYS["quit"]


def test_empty_hotkey_disables_action(tmp_path):
    """显式空串 = 禁用该热键（app 构建热键表时跳过空组合）。"""
    p = _write(tmp_path, {"hotkeys": {"quit": ""}})
    assert config.load_config(p)["hotkeys"]["quit"] == ""


# --------------------------------------------------------------------------- #
# 缓存
# --------------------------------------------------------------------------- #

def test_get_config_caches(monkeypatch):
    calls = []
    fake = {"hotkeys": dict(config.DEFAULT_HOTKEYS), "save_dir": "",
            "color": "#000000", "width": 1}

    def fake_load(path=None):
        calls.append(1)
        return fake

    monkeypatch.setattr(config, "_CACHE", None)
    monkeypatch.setattr(config, "load_config", fake_load)
    c1 = config.get_config()
    c2 = config.get_config()
    assert c1 is c2
    assert len(calls) == 1


# --------------------------------------------------------------------------- #
# 接入点：capture / selector / app
# --------------------------------------------------------------------------- #

def test_default_save_path_uses_configured_dir(monkeypatch, tmp_path):
    import capture
    monkeypatch.setattr(config, "_CACHE", {
        "hotkeys": dict(config.DEFAULT_HOTKEYS),
        "save_dir": str(tmp_path), "color": config.DEFAULT_COLOR,
        "width": config.DEFAULT_WIDTH,
    })
    p = capture.default_save_path()
    assert p.startswith(str(tmp_path) + "\\截图_")


def test_default_save_path_empty_dir_uses_desktop(monkeypatch):
    import capture
    monkeypatch.setattr(config, "_CACHE", None)   # 走隔离后的默认（save_dir=""）
    monkeypatch.setattr(capture, "get_desktop_path", lambda: "D:\\桌面")
    assert capture.default_save_path().startswith("D:\\桌面\\截图_")


def test_selector_uses_configured_color_and_width(qt_app, monkeypatch):
    import selector as sel_mod
    from selector import Selector
    monkeypatch.setattr(sel_mod, "_virtual_screen_size", lambda: (0, 0))
    monkeypatch.setattr(config, "_CACHE", {
        "hotkeys": dict(config.DEFAULT_HOTKEYS),
        "save_dir": "", "color": "#3370ff", "width": 7,
    })
    sel = Selector(x=0, y=0, width=100, height=100, full_img=None,
                   on_select=lambda r, i: None, on_copy=lambda r, i: None,
                   on_save=lambda r, i: None)
    try:
        assert sel.color == "#3370ff"
        assert sel.width_attr == 7
        assert sel._spin.value() == 7
    finally:
        sel._tb_hide()
        sel.close()


def test_app_hotkey_listener_falls_back_on_invalid_config(monkeypatch):
    """配置的热键组合 pynput 解析失败：回退默认热键，不阻断启动。"""
    import app
    sentinel = object()
    calls = []

    def fake_listener(hotkeys):
        calls.append(hotkeys)
        if len(calls) == 1:
            raise ValueError("invalid hotkey")
        return sentinel

    monkeypatch.setattr(app.kb, "GlobalHotKeys", fake_listener)
    actions = {"region": lambda: None, "fullscreen": lambda: None,
               "quit": lambda: None}
    result = app._make_hotkey_listener(actions, {"<bad>+x": actions["region"]})
    assert result is sentinel
    assert calls[1] == {config.DEFAULT_HOTKEYS[k]: fn
                        for k, fn in actions.items()}


def test_app_hotkey_listener_uses_configured_map(monkeypatch):
    import app
    sentinel = object()
    monkeypatch.setattr(app.kb, "GlobalHotKeys", lambda hk: sentinel)
    actions = {"region": lambda: None, "fullscreen": lambda: None,
               "quit": lambda: None}
    hotkeys = {"<ctrl>+<alt>+a": actions["region"]}
    assert app._make_hotkey_listener(actions, hotkeys) is sentinel
