"""Sandbox 内核预热代码，外置便于单测与维护。

`build_init_code(work_dir, hint_font, hint_path)` 返回组合后的纯 Python 源码字符串，
用于 jupyter / e2b 两种沙箱启动时一次性 exec。
"""
from .loader import CJK_RESULT_MARKER, build_init_code

__all__ = ["build_init_code", "CJK_RESULT_MARKER"]
