"""版本号一致性：src/version.py 与 version_info.txt（exe 版本资源）必须同步。"""
import re
from pathlib import Path

from src.version import __version__

_ROOT = Path(__file__).resolve().parent.parent


def test_version_format():
    """__version__ 为 X.Y.Z（发版打 v* 标签用）。"""
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)


def test_version_info_matches_version():
    text = (_ROOT / "version_info.txt").read_text(encoding="utf-8")
    expected4 = f"{__version__}.0"  # 版本资源为四段，补 0
    for field in ("FileVersion", "ProductVersion"):
        m = re.search(rf"StringStruct\(u'{field}', u'([^']+)'\)", text)
        assert m, f"version_info.txt 缺少 {field}"
        assert m.group(1) == expected4, (
            f"version_info.txt 的 {field}={m.group(1)!r} 与 "
            f"src/version.py 的 __version__={__version__!r} 不一致")
    m = re.search(r"filevers=\(([^)]+)\)", text)
    assert m and tuple(int(x) for x in m.group(1).split(",")) == tuple(
        int(x) for x in expected4.split("."))
