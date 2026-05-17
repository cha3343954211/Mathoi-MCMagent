"""sandbox 预热代码组装器单测。"""
from __future__ import annotations

from pathlib import Path

from app.sandbox.preamble import build_init_code
from app.sandbox.preamble.loader import parse_cjk_marker


def test_build_init_code_compiles():
    """组装出的代码必须是合法 Python，可被 compile() 接受。"""
    code = build_init_code(Path("/tmp/x"), hint_font="SimHei", hint_path="")
    compile(code, "<inject>", "exec")


def test_build_init_code_includes_hints():
    """提示参数应正确注入到生成代码中（Python 字面量）。"""
    code = build_init_code(Path("/tmp/x"), hint_font="MyFont", hint_path="/p/font.ttf")
    assert "_HINT_FONT = 'MyFont'" in code
    assert "_HINT_PATH = '/p/font.ttf'" in code


def test_build_init_code_chdir_path_is_escaped():
    """Windows 反斜杠应被转义为合法 Python 字符串。"""
    code = build_init_code(Path(r"C:\Users\x\work"), hint_font="", hint_path="")
    # 'os.chdir(...)' 中 backslash 必须双写
    assert "os.chdir('C:\\\\Users\\\\x\\\\work')" in code


def test_parse_cjk_marker_with_path():
    stdout = (
        "[Sandbox] CJK font: SimHei\n"
        "__MATHOI_CJK__:SimHei|C:/Windows/Fonts/simhei.ttf\n"
        "Sandbox ready\n"
    )
    f, p = parse_cjk_marker(stdout)
    assert f == "SimHei"
    assert p == "C:/Windows/Fonts/simhei.ttf"


def test_parse_cjk_marker_no_path():
    stdout = "__MATHOI_CJK__:SimHei|\n"
    f, p = parse_cjk_marker(stdout)
    assert f == "SimHei"
    assert p == ""


def test_parse_cjk_marker_no_font():
    """font 也为空（探测失败）应返回 ('','')。"""
    stdout = "[Sandbox] WARNING: 未找到 CJK 字体\n__MATHOI_CJK__:|\n"
    f, p = parse_cjk_marker(stdout)
    assert f == ""
    assert p == ""


def test_parse_cjk_marker_missing():
    """无标记行应返回 ('','')。"""
    f, p = parse_cjk_marker("just normal output")
    assert (f, p) == ("", "")


def test_parse_cjk_marker_takes_last_occurrence():
    """多行标记取最后一行（reload 场景）。"""
    stdout = "__MATHOI_CJK__:Old|/old.ttf\n__MATHOI_CJK__:New|/new.ttf\n"
    f, p = parse_cjk_marker(stdout)
    assert f == "New"
    assert p == "/new.ttf"
