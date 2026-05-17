"""Sandbox 预热代码组装器。

把分散的 .py 文件拼成一段可在 kernel 里 exec 的字符串：
    [外层基础 imports + chdir + cjk hint 变量赋值]
    [cjk_detect.py 内容]
    [mpl_setup.py 内容]

主进程读出 stdout 中的 `__MATHOI_CJK__:<font>|<path>` 行，回写本地缓存。
"""
from __future__ import annotations

from pathlib import Path

# stdout 标记前缀
CJK_RESULT_MARKER = "__MATHOI_CJK__:"

_PREAMBLE_DIR = Path(__file__).parent


def _read(name: str) -> str:
    return (_PREAMBLE_DIR / name).read_text(encoding="utf-8")


def build_init_code(
    work_dir: Path | str,
    *,
    hint_font: str = "",
    hint_path: str = "",
) -> str:
    """组装内核初始化代码字符串。

    参数：
    - work_dir: 内核 chdir 到的工作目录（可绝对路径）
    - hint_font/hint_path: 主进程从缓存读取的字体提示，传入 kernel 内仍会经
      `_verify_font` 校验，失败则降级到完整扫描
    """
    work_dir_str = str(work_dir).replace("\\", "\\\\")  # Windows 路径转义为合法 Python 字符串

    head = (
        "import os, sys, json, gc\n"
        "import warnings, logging\n"
        "warnings.filterwarnings('ignore')\n"
        # matplotlib.font_manager 通过 logging 而非 warnings 发出告警，
        # 单独压制，防止 'findfont: Font family ... not found' 污染 stderr
        "logging.getLogger('matplotlib.font_manager').setLevel(logging.ERROR)\n"
        "import matplotlib\n"
        "matplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        f"os.chdir('{work_dir_str}')\n"
        f"_HINT_FONT = {hint_font!r}\n"
        f"_HINT_PATH = {hint_path!r}\n"
        "\n"
    )
    cjk = "\n# ── CJK 字体探测 ──────────────────────────────────────\n" + _read("cjk_detect.py") + "\n"
    mpl = "\n# ── matplotlib / seaborn 全局风格 ─────────────────────\n" + _read("mpl_setup.py") + "\n"
    return head + cjk + mpl


def parse_cjk_marker(stdout: str) -> tuple[str, str]:
    """从 sandbox stdout 中提取最后一行 `__MATHOI_CJK__:font|path` 的解析结果。

    未找到时返回 ('', '')。
    """
    for line in reversed(stdout.splitlines()):
        if line.startswith(CJK_RESULT_MARKER):
            payload = line[len(CJK_RESULT_MARKER):].strip()
            if "|" in payload:
                f, p = payload.split("|", 1)
            else:
                f, p = payload, ""
            return f, p
    return "", ""
