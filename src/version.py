"""版本号（唯一事实来源）。

- 运行时：`from src.version import __version__`
- 打包时：`version_info.txt`（exe 的 Windows 版本资源，右键属性可见）
  必须与本文件保持一致，由 tests/test_version.py 守护。
- 发版时：更新本文件与 version_info.txt，再打 `v*` 标签触发正式 Release。
"""

__version__ = "1.0.0"
