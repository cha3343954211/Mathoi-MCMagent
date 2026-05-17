"""orchestrator 辅助函数测试：图表引用检测、命名/元数据 catalog、产物对账。"""
from __future__ import annotations

import json
from pathlib import Path

from app.workflow.orchestrator import (
    _build_figure_catalog,
    _diff_missing_figures,
    _ensure_all_figures_in_paper,
    _extract_expected_figures,
    _extract_referenced_images,
    _scope_to_question,
)

# ──────────────────────── _extract_referenced_images ────────────────────────

def test_extract_referenced_images_basic():
    md = "正文里提到 fig_q1_x.png 但没插图。\n\n![标题](fig_q1_y.png)\n"
    refs = _extract_referenced_images(md)
    assert refs == {"fig_q1_y.png"}    # 仅识别真正的 ![]() 嵌入


def test_extract_referenced_images_path_and_case():
    """basename 归一化 + 大小写不敏感。"""
    md = "![图1](sub/dir/Fig_EDA_Hist.PNG)\n\n![图2](./fig_q1_a.png)"
    refs = _extract_referenced_images(md)
    assert refs == {"fig_eda_hist.png", "fig_q1_a.png"}


def test_extract_referenced_images_with_title():
    """带标题 `[](url "title")` 的写法也支持。"""
    md = '![分布](fig_q1_dist.png "问题1分布")'
    refs = _extract_referenced_images(md)
    assert refs == {"fig_q1_dist.png"}


def test_extract_referenced_images_empty():
    assert _extract_referenced_images("") == set()
    assert _extract_referenced_images("plain text only") == set()


# ──────────────────────── _scope_to_question ────────────────────────────────

def test_scope_to_question():
    assert _scope_to_question("eda") == 0
    assert _scope_to_question("EDA") == 0
    assert _scope_to_question("sensitivity") == -1
    assert _scope_to_question("q1") == 1
    assert _scope_to_question("q12") == 12
    # 无效 / 空 scope 返回 -2，留待文件名兜底
    assert _scope_to_question("") == -2
    assert _scope_to_question("foo") == -2


# ──────────────────────── _build_figure_catalog ─────────────────────────────

def _touch_png(work_dir: Path, name: str) -> None:
    """生成最小有效 PNG 占位（1x1 透明像素）。"""
    PNG_1x1 = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\x0bIDATx\xdac````\x00\x00\x00\x05\x00\x01\xa5\xf6E@\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    (work_dir / name).write_bytes(PNG_1x1)


def test_catalog_from_filename_only(tmp_workspace: Path):
    """无 figures.jsonl 时按文件名前缀推断。"""
    _touch_png(tmp_workspace, "fig_eda_dist.png")
    _touch_png(tmp_workspace, "fig_q1_pred.png")
    _touch_png(tmp_workspace, "fig_q2_eval.png")
    _touch_png(tmp_workspace, "fig_sens_param.png")

    catalog = _build_figure_catalog(tmp_workspace)
    assert len(catalog) == 4
    by_q = {e["file"]: e["question"] for e in catalog}
    assert by_q["fig_eda_dist.png"] == 0
    assert by_q["fig_q1_pred.png"] == 1
    assert by_q["fig_q2_eval.png"] == 2
    assert by_q["fig_sens_param.png"] == -1
    # caption 默认从文件名美化
    eda = next(e for e in catalog if e["file"] == "fig_eda_dist.png")
    assert "fig eda dist" in eda["caption"]


def test_catalog_metadata_overrides_filename(tmp_workspace: Path):
    """figures.jsonl 中的 caption/scope 优先于文件名推断。"""
    _touch_png(tmp_workspace, "fig_q1_pred.png")
    _touch_png(tmp_workspace, "freestyle.png")    # 不规范命名
    meta_lines = [
        json.dumps({"file": "fig_q1_pred.png", "scope": "q1", "caption": "问题1预测"}),
        json.dumps({"file": "freestyle.png", "scope": "q2", "caption": "Q2 自由命名"}),
    ]
    (tmp_workspace / "figures.jsonl").write_text("\n".join(meta_lines), encoding="utf-8")

    catalog = _build_figure_catalog(tmp_workspace)
    assert len(catalog) == 2
    by_file = {e["file"]: e for e in catalog}
    assert by_file["fig_q1_pred.png"]["caption"] == "问题1预测"
    # freestyle.png 元数据 scope='q2' 应将其归到问题 2，而非文件名兜底的 0
    assert by_file["freestyle.png"]["question"] == 2
    assert by_file["freestyle.png"]["caption"] == "Q2 自由命名"


def test_catalog_invalid_scope_falls_back_to_filename(tmp_workspace: Path):
    """元数据 scope 无效时，应回退到文件名前缀。"""
    _touch_png(tmp_workspace, "fig_eda_x.png")
    (tmp_workspace / "figures.jsonl").write_text(
        json.dumps({"file": "fig_eda_x.png", "scope": "garbage", "caption": "覆盖标题"}) + "\n",
        encoding="utf-8",
    )
    catalog = _build_figure_catalog(tmp_workspace)
    e = catalog[0]
    assert e["question"] == 0     # 文件名兜底为 EDA
    assert e["caption"] == "覆盖标题"   # 但 caption 仍来自元数据


def test_catalog_corrupt_jsonl_does_not_crash(tmp_workspace: Path):
    """元数据文件损坏不应阻断 catalog 构建。"""
    _touch_png(tmp_workspace, "fig_q1_a.png")
    (tmp_workspace / "figures.jsonl").write_text(
        "{this is not valid json\n"
        + json.dumps({"file": "fig_q1_a.png", "scope": "q1", "caption": "OK"}) + "\n",
        encoding="utf-8",
    )
    catalog = _build_figure_catalog(tmp_workspace)
    assert len(catalog) == 1
    assert catalog[0]["caption"] == "OK"


# ──────────────────────── _extract_expected_figures ─────────────────────────

def test_extract_expected_figures_scopes():
    plan = """
EDA: 计划生成 fig_eda_dist.png 和 fig_eda_corr.png
问题1: fig_q1_pred.png, fig_q1_eval.png
问题2: fig_q2_cluster.png
敏感性: fig_sens_alpha.png
"""
    assert _extract_expected_figures(plan, scope="all") == {
        "fig_eda_dist.png", "fig_eda_corr.png",
        "fig_q1_pred.png", "fig_q1_eval.png",
        "fig_q2_cluster.png",
        "fig_sens_alpha.png",
    }
    assert _extract_expected_figures(plan, scope="eda") == {"fig_eda_dist.png", "fig_eda_corr.png"}
    assert _extract_expected_figures(plan, scope="q1") == {"fig_q1_pred.png", "fig_q1_eval.png"}
    assert _extract_expected_figures(plan, scope="sensitivity") == {"fig_sens_alpha.png"}


def test_diff_missing_figures():
    expected = {"fig_q1_a.png", "fig_q1_b.png", "fig_q1_c.png"}
    actual = {"fig_q1_a.png"}
    assert _diff_missing_figures(expected, actual) == ["fig_q1_b.png", "fig_q1_c.png"]
    assert _diff_missing_figures(set(), {"x"}) == []   # 无预期则无缺失


# ──────────────────────── _ensure_all_figures_in_paper ──────────────────────

def test_ensure_appends_missing_figures(tmp_workspace: Path):
    paper_md = tmp_workspace / "paper.md"
    paper_md.write_text(
        "# 论文\n\n## 五、数据预处理与描述性统计\n\n"
        "### 5.3 描述性统计分析\n\n正文。\n\n"
        "## 五、模型的建立与求解\n### 5.1 问题1模型的建立\n",
        encoding="utf-8",
    )
    catalog = [
        {"index": 1, "file": "fig_eda_dist.png", "question": 0,  "caption": "EDA 分布"},
        {"index": 2, "file": "fig_q1_pred.png",  "question": 1,  "caption": "问题1预测"},
    ]
    _ensure_all_figures_in_paper(paper_md, catalog)

    new = paper_md.read_text(encoding="utf-8")
    # 两张图都应被追加 ![](file)
    assert "![EDA 分布](fig_eda_dist.png)" in new
    assert "![问题1预测](fig_q1_pred.png)" in new


def test_ensure_does_not_duplicate_already_referenced(tmp_workspace: Path):
    """已通过 ![](file) 真正插入的图不应再次追加。"""
    paper_md = tmp_workspace / "paper.md"
    paper_md.write_text(
        "# 论文\n\n## 五、模型的建立与求解\n\n### 5.1 问题1\n"
        "![已插入](fig_q1_pred.png)\n",
        encoding="utf-8",
    )
    catalog = [{"index": 1, "file": "fig_q1_pred.png", "question": 1, "caption": "问题1预测"}]
    before = paper_md.read_text(encoding="utf-8")
    _ensure_all_figures_in_paper(paper_md, catalog)
    after = paper_md.read_text(encoding="utf-8")
    assert before == after    # 已引用，原文不变


def test_ensure_treats_prose_mention_as_unreferenced(tmp_workspace: Path):
    """正文里只复述文件名（无 ![]() 包裹）应仍视为未引用，需要追加。"""
    paper_md = tmp_workspace / "paper.md"
    paper_md.write_text(
        "# 论文\n\n## 五、模型的建立与求解\n\n### 5.1 问题1\n"
        "我们在生成时使用了 fig_q1_pred.png 作为输出文件。\n",
        encoding="utf-8",
    )
    catalog = [{"index": 1, "file": "fig_q1_pred.png", "question": 1, "caption": "问题1预测"}]
    _ensure_all_figures_in_paper(paper_md, catalog)
    after = paper_md.read_text(encoding="utf-8")
    assert "![问题1预测](fig_q1_pred.png)" in after
