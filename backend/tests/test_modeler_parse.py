"""Modeler 输出解析的单元测试。"""
from app.agents.modeler import parse_modeler_sections


def test_parse_full_sections():
    text = """
## EDA 数据分析方案
EDA content here.

## 问题1 建模方案
Q1 plan.

## 问题2 建模方案
Q2 plan.

## 敏感性分析方案
Sensitivity content.
"""
    out = parse_modeler_sections(text)
    assert set(out) == {"eda", "ques1", "ques2", "sensitivity_analysis"}
    assert "EDA content" in out["eda"]
    assert "Q1 plan" in out["ques1"]
    assert "Q2 plan" in out["ques2"]
    assert "Sensitivity content" in out["sensitivity_analysis"]


def test_parse_aliases():
    """常见别名（数据分析 / 灵敏度）应被识别。"""
    text = """
## 数据分析阶段
内容A

## 问题1 描述
内容B

## 灵敏度分析
内容C
"""
    out = parse_modeler_sections(text)
    assert "eda" in out
    assert out["eda"].startswith("内容A")
    assert "ques1" in out
    assert "sensitivity_analysis" in out


def test_parse_unknown_falls_back_to_raw():
    """完全不匹配的标题应触发 _raw 兜底，而非返回空 dict。"""
    text = """
## 完全自创的标题
some content
"""
    out = parse_modeler_sections(text)
    assert out == {"_raw": text}


def test_parse_empty_input():
    out = parse_modeler_sections("")
    assert out == {"_raw": ""}


def test_parse_question_number_extraction():
    """ques 编号应从 '问题N' 中正确提取，与 N 是否大于 9 无关。"""
    text = """
## 问题1 建模方案
A
## 问题12 建模方案
B
"""
    out = parse_modeler_sections(text)
    assert "ques1" in out
    assert "ques12" in out
