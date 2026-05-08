"""Writer 分节重试模块。

从磁盘重建 Writer 上下文（无需重跑 Modeler/Coder），只重写指定章节，
然后重组 paper.md。由 API 端点 POST /tasks/{id}/rewrite-section 调用。
"""
from __future__ import annotations

import json
from pathlib import Path

from ..agents import WriterAgent
from ..agents.prompts import (
    WRITER_SECTION_ABSTRACT,
    WRITER_SECTION_ANALYSIS,
    WRITER_SECTION_ASSUMPTIONS,
    WRITER_SECTION_EDA,
    WRITER_SECTION_EVALUATION,
    WRITER_SECTION_RESTATEMENT,
    WRITER_SECTION_SENSITIVITY,
)
from ..core.config import get_settings
from ..core.events import EventType, emit
from ..core.logging import logger
from ..tasks import TaskState, task_manager
from ..tools import build_writer_registry
from .orchestrator import (
    _build_figure_catalog,
    _collect_coder_reports,
    _ensure_all_figures_in_paper,
    _extract_expected_figures,
    _actual_figures,
    _diff_missing_figures,
    _format_catalog_for_writer,
    _load_md_template,
    _patch_agent_with_hitl,
    _REPORT_MAX_CHARS,
    SECTION_MAP,
)

# ── 章节 key → sec 文件 映射（固定章节） ─────────────────────────────────────
# 问题章节 q1/q2/... 动态生成，不在此表中

_WRITER_SECTION_PROMPTS = {
    "abstract":    WRITER_SECTION_ABSTRACT,
    "restatement": WRITER_SECTION_RESTATEMENT,
    "analysis":    WRITER_SECTION_ANALYSIS,
    "assumptions": WRITER_SECTION_ASSUMPTIONS,
    "symbol":      "",
    "eda":         WRITER_SECTION_EDA,
    "sensitivity": WRITER_SECTION_SENSITIVITY,
    "evaluation":  WRITER_SECTION_EVALUATION,
}


async def run_rewrite_section(task_id: str, section_key: str, user_id: int) -> None:
    """重写单个 Writer 章节并重组 paper.md。"""
    task = task_manager.get(task_id)
    if not task or not task.work_dir:
        raise ValueError(f"task {task_id} not found or has no work_dir")

    work_dir = Path(task.work_dir)

    # 必要上下文文件
    q_file = work_dir / "questions.json"
    mp_file = work_dir / "modeling_plan.md"
    if not q_file.exists() or not mp_file.exists():
        raise ValueError("缺少必要上下文文件（questions.json / modeling_plan.md）")

    questions: dict = json.loads(q_file.read_text(encoding="utf-8"))
    ques_count: int = int(questions.get("ques_count", 1))
    modeling_plan: str = mp_file.read_text(encoding="utf-8")

    # 解析 section_key：固定章节 or q{N}
    is_question = section_key.startswith("q") and section_key[1:].isdigit()
    qi = int(section_key[1:]) if is_question else 0

    if not is_question and section_key not in SECTION_MAP:
        raise ValueError(f"未知章节 key: {section_key}")

    sec_file, phase_label = (
        (f"sec_q{qi}.md", f"writer:q{qi}")
        if is_question
        else SECTION_MAP[section_key]
    )

    await task_manager.update_state(task_id, TaskState.RUNNING, phase=f"rewrite:{section_key}")
    await emit(EventType.PHASE_ENTER, task_id, phase=f"rewrite:{section_key}")

    try:
        # ── 重建公共上下文 ────────────────────────────────────────────────────
        coder_reports = _collect_coder_reports(work_dir, ques_count)
        figure_catalog = _build_figure_catalog(work_dir)
        catalog_text   = _format_catalog_for_writer(figure_catalog)

        bg_questions = "\n".join(
            f"**问题{i}**：{questions.get(f'ques{i}', '')}"
            for i in range(1, ques_count + 1)
        )

        all_expected = _extract_expected_figures(modeling_plan, scope="all")
        all_actual   = _actual_figures(work_dir)
        final_missing = _diff_missing_figures(all_expected, all_actual)
        missing_section = ""
        if final_missing:
            missing_section = (
                "# ⚠️ 缺失图表清单（**严禁引用**）\n"
                + "\n".join(f"- `{f}`" for f in final_missing) + "\n\n"
            )

        base_ctx = (
            f"# 题目背景\n{questions.get('background', '')}\n\n"
            f"# 各问题描述\n{bg_questions}\n\n"
            f"# 建模方案（Modeler 输出）\n{modeling_plan}\n\n"
            f"# 模型建立与求解（Coder 报告）\n{coder_reports}\n\n"
            f"# ✅ 实际可用图表目录（共 {len(figure_catalog)} 张）\n{catalog_text}\n\n"
            + missing_section
            + "# 图表引用铁律\n"
            "- 只允许引用「实际可用图表目录」中列出的文件名；\n"
            "- 引用未列出的图片视为严重错误。\n\n"
        )

        tpl = _load_md_template()

        # ── 生成本节 prompt ───────────────────────────────────────────────────
        if is_question:
            ques_tpl = tpl.get(f"ques{qi}", "")
            if not ques_tpl:
                header = "# 五、模型的建立与求解\n\n" if qi == 1 else ""
                ques_tpl = (
                    f"{header}## 5.{qi} 问题{qi}模型的建立与求解\n\n"
                    f"### 5.{qi}.1 模型的建立\n### 5.{qi}.2 模型的求解\n约600字实质内容。"
                )
            ques_figs = [e for e in figure_catalog if e.get("question") == qi]
            ques_catalog = _format_catalog_for_writer(ques_figs)
            rp = work_dir / f"result_q{qi}.md"
            ques_result = ""
            if rp.exists():
                raw = rp.read_text(encoding="utf-8")
                ques_result = raw[:_REPORT_MAX_CHARS] + "…（已截断）" if len(raw) > _REPORT_MAX_CHARS else raw
            prompt = "\n".join([
                ques_tpl, "",
                f"### 问题{qi}描述", questions.get(f"ques{qi}", ""), "",
                f"### Coder 求解报告（问题{qi}）",
                ques_result or "（无报告）", "",
                "### 本问图表目录（必须全部插入）",
                ques_catalog or "（本问暂无图表）",
            ])

        elif section_key == "eda":
            eda_rp = work_dir / "eda_report.md"
            eda_report = ""
            if eda_rp.exists():
                raw = eda_rp.read_text(encoding="utf-8")
                eda_report = raw[:_REPORT_MAX_CHARS] + "…" if len(raw) > _REPORT_MAX_CHARS else raw
            eda_figs = [e for e in figure_catalog if e.get("question") == 0]
            eda_catalog = _format_catalog_for_writer(eda_figs)
            prompt = (
                tpl.get("eda", WRITER_SECTION_EDA)
                + f"\n\n# EDA 分析报告\n{eda_report}\n\n"
                + f"# EDA 图表目录\n{eda_catalog}\n"
            )

        elif section_key == "sensitivity":
            sens_p = work_dir / "sensitivity_report.md"
            sens_report = ""
            if sens_p.exists():
                raw = sens_p.read_text(encoding="utf-8")
                sens_report = raw[:_REPORT_MAX_CHARS] + "…" if len(raw) > _REPORT_MAX_CHARS else raw
            sens_figs = [e for e in figure_catalog if e.get("question") == -1]
            sens_catalog = _format_catalog_for_writer(sens_figs)
            prompt = (
                tpl.get("sensitivity_analysis", WRITER_SECTION_SENSITIVITY)
                + f"\n\n# 敏感性分析报告\n{sens_report}\n\n"
                + f"# 敏感性图表目录\n{sens_catalog}\n"
            )

        else:
            # 固定章节：直接用 TOML 模板或默认 prompt
            tpl_keys = {
                "abstract":    "firstPage",
                "restatement": "RepeatQues",
                "analysis":    "analysisQues",
                "assumptions": "modelAssumption",
                "symbol":      "symbol",
                "evaluation":  "judge",
            }
            prompt = tpl.get(tpl_keys.get(section_key, ""), _WRITER_SECTION_PROMPTS.get(section_key, ""))

        # ── 获取 OpenAlex email ───────────────────────────────────────────────
        settings = get_settings()
        openalex_email = settings.openalex_email or ""
        try:
            from ..db import AsyncSessionLocal, SystemSetting
            from sqlalchemy import select as _sel
            async with AsyncSessionLocal() as _ss:
                row = (await _ss.execute(
                    _sel(SystemSetting).where(SystemSetting.key == "openalex_email")
                )).scalar_one_or_none()
                if row and row.value.strip():
                    openalex_email = row.value.strip()
        except Exception:
            pass

        writer_tools = build_writer_registry(work_dir, openalex_email=openalex_email)

        # ── 调用 WriterAgent ──────────────────────────────────────────────────
        w = WriterAgent(task_id=task_id, user_id=user_id,
                        tools=writer_tools, max_iterations=6)
        _patch_agent_with_hitl(w, task_id)
        output = await w.run(base_ctx + prompt)

        # 强制覆盖（重写语义：无论文件是否存在都替换）
        sec_path = work_dir / sec_file
        sec_path.write_text(output, encoding="utf-8")
        logger.info("Section rewritten: {} | task={}", sec_file, task_id)

        # ── 重组 paper.md ─────────────────────────────────────────────────────
        _rebuild_paper(work_dir, ques_count, questions)

        await emit(EventType.PHASE_EXIT, task_id, phase=f"rewrite:{section_key}")
        await task_manager.update_state(task_id, TaskState.COMPLETED)
        await emit(EventType.TASK_COMPLETED, task_id)

    except Exception as exc:
        logger.exception("rewrite_section failed | task={}", task_id)
        await emit(EventType.PHASE_EXIT, task_id, phase=f"rewrite:{section_key}")
        await task_manager.update_state(task_id, TaskState.FAILED, error=str(exc))
        await emit(EventType.TASK_FAILED, task_id, error=str(exc))
        raise


def _rebuild_paper(work_dir: Path, ques_count: int, questions: dict) -> None:
    """按章节文件顺序重组 paper.md。"""
    ordered = [
        "sec_abstract.md",
        "sec_restatement.md",
        "sec_analysis.md",
        "sec_assumptions.md",
        "sec_symbol.md",
        "sec_eda.md",
        *[f"sec_q{i}.md" for i in range(1, ques_count + 1)],
        "sec_sensitivity.md",
        "sec_evaluation.md",
    ]
    sections = []
    for fname in ordered:
        p = work_dir / fname
        if p.exists() and p.stat().st_size > 10:
            sections.append(p.read_text(encoding="utf-8").strip())

    title = questions.get("title", "数学建模论文")
    paper_md = work_dir / "paper.md"
    paper_md.write_text(
        f"# {title}\n\n" + "\n\n---\n\n".join(s for s in sections if s),
        encoding="utf-8",
    )

    # 后处理：确保所有图片被引用
    figure_catalog = _build_figure_catalog(work_dir)
    _ensure_all_figures_in_paper(paper_md, figure_catalog)
