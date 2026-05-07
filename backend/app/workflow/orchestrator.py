"""
主工作流：Coordinator → Modeler → Coder(EDA/各问/敏感性) → Writer(分节合并)
所有 Agent 调用均带 user_id，自动按用户配置解析并计量。
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from ..agents import CoderAgent, CoordinatorAgent, ModelerAgent, WriterAgent
from ..agents.prompts import (
    CODER_EDA_PROMPT,
    CODER_SENSITIVITY_PROMPT,
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
from ..exporters import export_paper
from ..sandbox import JupyterSandbox
from ..llm import image_part_from_file
from ..tasks import TaskState, task_manager
from ..tools import build_default_registry

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".svg", ".webp"}
_MULTIMODAL_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def _patch_agent_with_hitl(agent, task_id: str) -> None:
    async def wait_if_paused() -> None:
        await task_manager.wait_if_paused(task_id)
    agent.wait_if_paused = wait_if_paused  # type: ignore[assignment]


async def run_workflow(task_id: str) -> None:
    settings = get_settings()
    task = task_manager.get(task_id)
    if not task:
        logger.error("task {} not found", task_id)
        return

    work_dir = Path(task.work_dir)
    uid = task.user_id
    await task_manager.update_state(task_id, TaskState.RUNNING, phase="init")
    await emit(EventType.TASK_STARTED, task_id)

    try:
        async with JupyterSandbox(task_id, work_dir) as sandbox:
            tools = build_default_registry(sandbox, work_dir)
            data_summary = _describe_data(work_dir, task.data_files)

            # ── Phase 0: Coordinator（问题结构化）────────────────────────
            await task_manager.update_state(task_id, TaskState.RUNNING, phase="coordinator")
            await emit(EventType.PHASE_ENTER, task_id, phase="coordinator")

            # 读取随题上传的图片
            images = [
                image_part_from_file(work_dir / f)
                for f in (task.image_files or [])
                if (work_dir / f).exists()
                   and Path(f).suffix.lower() in _MULTIMODAL_EXTS
            ]
            coordinator = CoordinatorAgent(task_id=task_id, user_id=uid)
            _patch_agent_with_hitl(coordinator, task_id)
            questions = await coordinator.run(task.problem, images=images or None)
            ques_count: int = int(questions.get("ques_count", 1))
            (work_dir / "questions.json").write_text(
                json.dumps(questions, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            await task_manager.checkpoint(task_id, "coordinator_done", {"ques_count": ques_count})
            await emit(EventType.PHASE_EXIT, task_id, phase="coordinator")

            # ── Phase 1: Modeler（建模方案）──────────────────────────────
            await task_manager.wait_if_paused(task_id)  # 届间检查点
            await task_manager.update_state(task_id, TaskState.RUNNING, phase="modeler")
            await emit(EventType.PHASE_ENTER, task_id, phase="modeler")

            modeler_input = (
                f"# 题目结构化信息\n```json\n{json.dumps(questions, ensure_ascii=False, indent=2)}\n```\n\n"
                f"# 工作区数据文件\n{data_summary}\n\n"
                f"题目共 {ques_count} 个问题，请为每个问题制定详细建模方案，"
                "并包含 EDA 分析方案和敏感性分析方案。"
            )
            modeler = ModelerAgent(task_id=task_id, user_id=uid, tools=None, max_iterations=3)
            _patch_agent_with_hitl(modeler, task_id)
            modeling_plan = await modeler.run(modeler_input)
            (work_dir / "modeling_plan.md").write_text(modeling_plan, encoding="utf-8")

            # 解析 Modeler 结构化 JSON，提取各阶段专属方案
            solutions: dict = {}
            try:
                solutions = json.loads(modeling_plan)
                if not isinstance(solutions, dict):
                    solutions = {}
            except (json.JSONDecodeError, ValueError):
                solutions = {}   # 兜底：将使用全文作为通用方案

            def _solution_ctx(key: str) -> str:
                """提取指定 key 的建模方案，不存在时降级到全文。"""
                plan = solutions.get(key, "")
                if plan:
                    return f"# 建模方案（{key}）\n{plan}\n\n"
                # 兜底：全文
                return f"# 建模方案\n{modeling_plan}\n\n"

            data_ctx = f"# 工作区数据文件\n{data_summary}\n\n"
            await task_manager.checkpoint(task_id, "modeler_done", {"keys": list(solutions.keys())})
            await emit(EventType.PHASE_EXIT, task_id, phase="modeler")
            # ── Phase 2: Coder（分流执行）────────────────────────────────
            await task_manager.wait_if_paused(task_id)  # 届间检查点
            await task_manager.update_state(task_id, TaskState.RUNNING, phase="coder")
            await emit(EventType.PHASE_ENTER, task_id, phase="coder")

            # stdout 收集：记录各 Coder 阶段的图表数据特征输出
            coder_stdout_log: list[str] = []

            # 2a: EDA（只传 eda 方案）
            await emit(EventType.PHASE_ENTER, task_id, phase="coder:eda")
            coder_eda = CoderAgent(task_id=task_id, user_id=uid, tools=tools,
                                   max_iterations=settings.max_coder_iterations)
            _patch_agent_with_hitl(coder_eda, task_id)
            try:
                eda_out = await coder_eda.run(_solution_ctx("eda") + data_ctx + CODER_EDA_PROMPT)
                coder_stdout_log.append(_extract_figure_features(eda_out, label="EDA"))
            except Exception as _e:
                logger.warning("coder:eda failed (non-fatal): {}", _e)
            await emit(EventType.PHASE_EXIT, task_id, phase="coder:eda")

            # 2b: 逐问求解（只传对应问题的方案）
            await task_manager.wait_if_paused(task_id)  # 届间检查点
            for qi in range(1, ques_count + 1):
                ques_key = f"ques{qi}"
                ques_text = questions.get(ques_key, f"问题{qi}")
                await emit(EventType.PHASE_ENTER, task_id, phase=f"coder:q{qi}")
                coder_q = CoderAgent(task_id=task_id, user_id=uid, tools=tools,
                                     max_iterations=settings.max_coder_iterations)
                _patch_agent_with_hitl(coder_q, task_id)
                eda_ctx = ""
                eda_p = work_dir / "eda_report.md"
                if eda_p.exists():
                    eda_ctx = f"\n# EDA 分析报告\n{eda_p.read_text(encoding='utf-8')[:8000]}\n"
                q_prompt = (
                    _solution_ctx(ques_key) + data_ctx + eda_ctx +
                    f"## 当前任务：求解问题 {qi}\n\n"
                    f"问题描述：{ques_text}\n\n"
                    f"完整实现求解、可视化并保存结果。\n"
                    f"图表命名：`fig_q{qi}_*.png`。\n"
                    f"最后用 `write_file` 保存 `result_q{qi}.md`（含关键数值结论），再回复 `TASK_COMPLETE`。"
                )
                try:
                    q_out = await coder_q.run(q_prompt)
                    coder_stdout_log.append(_extract_figure_features(q_out, label=f"问题{qi}"))
                except Exception as _e:
                    logger.warning("coder:q{} failed (non-fatal): {}", qi, _e)
                await emit(EventType.PHASE_EXIT, task_id, phase=f"coder:q{qi}")
                await task_manager.wait_if_paused(task_id)  # 逐问间检查点

            # 2c: 敏感性分析（只传 sensitivity_analysis 方案）
            await emit(EventType.PHASE_ENTER, task_id, phase="coder:sensitivity")
            coder_sens = CoderAgent(task_id=task_id, user_id=uid, tools=tools,
                                    max_iterations=settings.max_coder_iterations)
            _patch_agent_with_hitl(coder_sens, task_id)
            try:
                sens_out = await coder_sens.run(
                    _solution_ctx("sensitivity_analysis") + data_ctx + CODER_SENSITIVITY_PROMPT
                )
                coder_stdout_log.append(_extract_figure_features(sens_out, label="敏感性分析"))
            except Exception as _e:
                logger.warning("coder:sensitivity failed (non-fatal): {}", _e)
            await emit(EventType.PHASE_EXIT, task_id, phase="coder:sensitivity")

            await task_manager.checkpoint(task_id, "coder_done", {})
            await emit(EventType.PHASE_EXIT, task_id, phase="coder")

            # ── Phase 3: Writer（分节写作 → 合并）───────────────────────
            await task_manager.wait_if_paused(task_id)  # 届间检查点
            await task_manager.update_state(task_id, TaskState.RUNNING, phase="writer")
            await emit(EventType.PHASE_ENTER, task_id, phase="writer")

            # 收集 Coder 产出的报告
            coder_reports = _collect_coder_reports(work_dir, ques_count)
            figure_catalog = _build_figure_catalog(work_dir)
            catalog_text   = _format_catalog_for_writer(figure_catalog)

            # 汇总 Coder 各阶段图表数据特征
            figure_features_text = "\n\n".join(s for s in coder_stdout_log if s.strip())

            # 加载论文写作模板结构参考
            template_ctx = _load_paper_template()

            writer_ctx = (
                (f"# 论文写作模板参考\n{template_ctx}\n\n---\n\n" if template_ctx else "")
                + f"# 题目背景\n{questions.get('background', task.problem)}\n\n"
                + f"# 各问题描述\n"
                + "\n".join(f"**问题{i}**：{questions.get(f'ques{i}','')}" for i in range(1, ques_count + 1))
                + f"\n\n# 建模方案\n{modeling_plan}\n\n"
                + f"# Coder 分析报告\n{coder_reports}\n\n"
                + (f"# 图表数据特征（Coder print 输出，用于精确描述图表内容）\n{figure_features_text}\n\n"
                   if figure_features_text.strip() else "")
                + f"# 图表目录（共 {len(figure_catalog)} 张，每张均须插入论文）\n{catalog_text}\n\n"
            )

            # 分节写作（Writer 不用工具，直接返回文本，框架负责保存）
            def _save_section(sec_file: str, content: str) -> str:
                p = work_dir / sec_file
                # 若 LLM 已调用 write_file 则以文件为准，否则用返回值兜底
                if not p.exists() or p.stat().st_size < 50:
                    p.write_text(content, encoding="utf-8")
                return p.read_text(encoding="utf-8")

            sections: list[str] = []
            writer_sections = [
                ("writer:abstract",    WRITER_SECTION_ABSTRACT,    "sec_abstract.md"),
                ("writer:restatement", WRITER_SECTION_RESTATEMENT, "sec_restatement.md"),
                ("writer:analysis",    WRITER_SECTION_ANALYSIS,    "sec_analysis.md"),
                ("writer:assumptions", WRITER_SECTION_ASSUMPTIONS, "sec_assumptions.md"),
                ("writer:eda",         WRITER_SECTION_EDA,         "sec_eda.md"),
            ]
            for phase_name, section_prompt, sec_file in writer_sections:
                await emit(EventType.PHASE_ENTER, task_id, phase=phase_name)
                try:
                    w = WriterAgent(task_id=task_id, user_id=uid, tools=None, max_iterations=4)
                    _patch_agent_with_hitl(w, task_id)
                    output = await w.run(writer_ctx + section_prompt)
                    sections.append(_save_section(sec_file, output))
                except Exception as _e:
                    logger.warning("writer {} failed (non-fatal): {}", phase_name, _e)
                await emit(EventType.PHASE_EXIT, task_id, phase=phase_name)
                await task_manager.wait_if_paused(task_id)  # 届间检查点

            # 逐问写作
            for qi in range(1, ques_count + 1):
                phase_name = f"writer:q{qi}"
                await emit(EventType.PHASE_ENTER, task_id, phase=phase_name)
                ques_figs = [e for e in figure_catalog if e.get("question") == qi]
                ques_catalog = _format_catalog_for_writer(ques_figs)
                ques_result = ""
                rp = work_dir / f"result_q{qi}.md"
                if rp.exists():
                    ques_result = rp.read_text(encoding="utf-8")
                q_sec_prompt = "\n".join([
                    f"撰写论文【问题{qi}：建模与求解】章节，保存到 `sec_q{qi}.md`。",
                    "",
                    f"### 问题描述",
                    questions.get(f"ques{qi}", ""),
                    "",
                    f"### Coder 求解报告（含全部关键数值）",
                    ques_result or "（无报告，依据建模方案推断）",
                    "",
                    f"### 本问图表目录（必须全部插入正文，一张不得遗漏）",
                    ques_catalog,
                    "",
                    "### 写作要求（必须全部满足）",
                    f"1. 正文不少于 800 字实质内容（不含图表标注）；",
                    f"2. 严格按以下结构输出：",
                    f"   `## 问题{qi}：[自拟标题]`",
                    f"   `### {qi}.1 问题分析与建模思路`（150-200字：难点分析、方法选择理由、与备选方法对比）",
                    f"   `### {qi}.2 模型建立`（完整数学公式推导：变量定义→目标函数→约束条件）",
                    f"   `### {qi}.3 求解过程`（算法步骤、迭代逻辑、关键参数设置）",
                    f"   `### {qi}.4 结果分析`（逐项引用 Coder 报告数值，插入全部图表）",
                    f"   `### {qi}.5 本问小结`（50-80字，归纳核心结论）",
                    f"3. 每个模型公式独立成行 `$$...$$`，变量首次出现时用 `$...$` 定义含义；",
                    f"4. 每张图前2-3句分析铺垫，图后1-2句结论，图表标注严格用规定格式；",
                    f"5. 所有数值来自 Coder 报告，精确到4位有效数字，不得编造。",
                ])
                try:
                    w = WriterAgent(task_id=task_id, user_id=uid, tools=None, max_iterations=4)
                    _patch_agent_with_hitl(w, task_id)
                    output = await w.run(writer_ctx + q_sec_prompt)
                    sections.append(_save_section(f"sec_q{qi}.md", output))
                except Exception as _e:
                    logger.warning("writer:q{} failed (non-fatal): {}", qi, _e)
                await emit(EventType.PHASE_EXIT, task_id, phase=phase_name)
                await task_manager.wait_if_paused(task_id)  # 逐问间检查点

            # 敏感性分析节
            await emit(EventType.PHASE_ENTER, task_id, phase="writer:sensitivity")
            sens_p = work_dir / "sensitivity_report.md"
            sens_ctx = ""
            if sens_p.exists():
                sens_ctx = f"\n# 敏感性分析报告\n{sens_p.read_text(encoding='utf-8')[:8000]}\n"
            sens_figs = [e for e in figure_catalog if e.get("question") == -1]
            sens_catalog = _format_catalog_for_writer(sens_figs)
            try:
                w = WriterAgent(task_id=task_id, user_id=uid, tools=None, max_iterations=4)
                _patch_agent_with_hitl(w, task_id)
                output = await w.run(
                    writer_ctx + sens_ctx
                    + f"\n# 敏感性分析图表（必须全部插入）\n{sens_catalog}\n\n"
                    + WRITER_SECTION_SENSITIVITY
                )
                sections.append(_save_section("sec_sensitivity.md", output))
            except Exception as _e:
                logger.warning("writer:sensitivity failed (non-fatal): {}", _e)
            await emit(EventType.PHASE_EXIT, task_id, phase="writer:sensitivity")
            await task_manager.wait_if_paused(task_id)

            # 模型评价节
            await emit(EventType.PHASE_ENTER, task_id, phase="writer:evaluation")
            try:
                w = WriterAgent(task_id=task_id, user_id=uid, tools=None, max_iterations=4)
                _patch_agent_with_hitl(w, task_id)
                output = await w.run(writer_ctx + WRITER_SECTION_EVALUATION)
                sections.append(_save_section("sec_evaluation.md", output))
            except Exception as _e:
                logger.warning("writer:evaluation failed (non-fatal): {}", _e)
            await emit(EventType.PHASE_EXIT, task_id, phase="writer:evaluation")

            # 合并所有节 → paper.md
            paper_md = work_dir / "paper.md"
            title = questions.get("title", "数学建模论文")
            full_paper = f"# {title}\n\n" + "\n\n---\n\n".join(s for s in sections if s.strip())
            paper_md.write_text(full_paper, encoding="utf-8")

            # 后处理：确保所有图片被引用
            _ensure_all_figures_in_paper(paper_md, figure_catalog)

            # 导出 docx
            try:
                docx_path = export_paper(paper_md, work_dir / "paper.docx")
                logger.info("Paper exported: {}", docx_path)
            except Exception as e:
                logger.warning("docx export failed: {}", e)

            await emit(EventType.PHASE_EXIT, task_id, phase="writer")
            await task_manager.update_state(task_id, TaskState.COMPLETED)
            await emit(EventType.TASK_COMPLETED, task_id)

    except asyncio.CancelledError:
        # 任务被主动取消（cancel() 已写入 CANCELLED 状态并发出事件，这里只需清理）
        logger.info("workflow cancelled | task={}", task_id)
        raise  # 让 asyncio 正常完成 Task 的取消流程
    except Exception as e:
        logger.exception("workflow failed")
        await task_manager.update_state(task_id, TaskState.FAILED, error=str(e))
        await emit(EventType.TASK_FAILED, task_id, error=str(e))


# ── 辅助函数 ─────────────────────────────────────────────────────────────────

def _describe_data(work_dir: Path, files: list[str]) -> str:
    if not files:
        return "（无数据文件）"
    lines = []
    for f in files:
        p = work_dir / f
        if p.exists():
            lines.append(f"- `{f}`（{p.stat().st_size} bytes）")
        else:
            lines.append(f"- `{f}`（缺失）")
    return "\n".join(lines)


def _collect_coder_reports(work_dir: Path, ques_count: int) -> str:
    """收集 Coder 各阶段产出的报告文件，合并为字符串。"""
    parts: list[str] = []
    report_files = (
        ["eda_report.md"]
        + [f"result_q{i}.md" for i in range(1, ques_count + 1)]
        + ["sensitivity_report.md"]
    )
    for fname in report_files:
        p = work_dir / fname
        if p.exists():
            content = p.read_text(encoding="utf-8")
            parts.append(f"### {fname}\n{content}")
    return "\n\n".join(parts) if parts else "（暂无 Coder 报告）"


def _build_figure_catalog(work_dir: Path) -> list[dict]:
    """
    扫描工作区所有图片，根据命名规范推断所属问题编号。
    命名规范：
      - fig_eda_xxx.png  → question=0（EDA）
      - fig_q1_xxx.png   → question=1
      - fig_sens_xxx.png → question=-1（敏感性）
    返回 list[{index, file, question, caption}]
    """
    existing = [
        p for p in work_dir.iterdir()
        if p.is_file() and p.suffix.lower() in _IMAGE_EXTS
    ]
    catalog: list[dict] = []
    for p in existing:
        fname = p.name
        qn = 0
        if fname.startswith("fig_eda"):
            qn = 0
        elif fname.startswith("fig_sens"):
            qn = -1
        else:
            m = re.match(r"fig_q(\d+)", fname)
            if m:
                qn = int(m.group(1))
        caption = fname.rsplit(".", 1)[0].replace("_", " ")
        catalog.append({"file": fname, "question": qn, "caption": caption})

    catalog.sort(key=lambda e: (e["question"] if e["question"] >= 0 else 999, e["file"]))
    for i, e in enumerate(catalog, 1):
        e["index"] = i
    return catalog


def _format_catalog_for_writer(catalog: list[dict]) -> str:
    """生成给 Writer 的纯文本图表清单。"""
    if not catalog:
        return "（本次运行未生成图片）"
    lines = []
    for e in catalog:
        qstr = f"[问题{e['question']}]" if e["question"] else ""
        desc = f" — {e['desc']}" if e.get("desc") else ""
        lines.append(
            f"- 图{e['index']} {qstr} `{e['file']}` caption=\"{e['caption']}\"{desc}"
        )
    return "\n".join(lines)


def _load_paper_template() -> str:
    """加载 config/paper_template.md，失败则返回空字符串。"""
    try:
        tpl = Path(__file__).parent.parent / "config" / "paper_template.md"
        if tpl.exists():
            return tpl.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("paper_template.md 加载失败: {}", e)
    return ""


def _extract_figure_features(agent_output: str, label: str = "") -> str:
    """从 Agent 返回文本中提取 【图数据特征】 和 【建模结果汇总】 块，供 Writer 使用。
    若无特征输出则返回空字符串。
    """
    if not agent_output:
        return ""
    import re as _re
    # 匹配 【...】 开头的特征块（多行，直到下一个空行或文末）
    pattern = _re.compile(
        r"(【(?:图数据特征|建模结果汇总)[^\n]*】.*?)(?=\n\n|\Z)",
        _re.DOTALL,
    )
    matches = pattern.findall(agent_output)
    if not matches:
        return ""
    prefix = f"## {label}\n" if label else ""
    return prefix + "\n\n".join(m.strip() for m in matches)


def _ensure_all_figures_in_paper(paper_md: Path, catalog: list[dict]) -> None:
    """后处理：把 catalog 中未被 paper.md 引用的图追加到文末，避免遗漏。"""
    content = paper_md.read_text(encoding="utf-8")
    missing = [e for e in catalog if e["file"] not in content]
    if not missing:
        return

    logger.warning("paper.md 遗漏 {} 张图，自动追加", len(missing))
    appendix = "\n\n## 附录：补充图表\n\n"
    for e in missing:
        appendix += (
            f"如图{e['index']}所示，该图为 {e['caption']}。\n\n"
            f"![{e['caption']}]({e['file']})\n\n"
            f"**图{e['index']}：{e['caption']}**\n\n"
        )
    paper_md.write_text(content + appendix, encoding="utf-8")
