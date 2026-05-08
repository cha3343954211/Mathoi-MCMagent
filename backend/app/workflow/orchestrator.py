"""
主工作流：Coordinator → Modeler → Coder(EDA/各问/敏感性) → Writer(分节合并)
所有 Agent 调用均带 user_id，自动按用户配置解析并计量。
"""
from __future__ import annotations

import asyncio
import json
import re
from collections import defaultdict
from pathlib import Path

from ..agents import CoderAgent, CoordinatorAgent, ModelerAgent, WriterAgent
from ..agents.modeler import parse_modeler_sections
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
from ..tools import build_default_registry, build_writer_registry
from ..utils import build_data_preview

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
            task_manager.register_sandbox(task_id, sandbox)   # 中断接口可用
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

            # ── Phase 1: Modeler（建模方案 + HITL 人工审核）────────────
            await task_manager.wait_if_paused(task_id)
            await task_manager.update_state(task_id, TaskState.RUNNING, phase="modeler")
            await emit(EventType.PHASE_ENTER, task_id, phase="modeler")

            # 数据预扫：提取 schema / head(5) / 缺失率，仅在有数据文件时生成
            data_preview_md = build_data_preview(work_dir, task.data_files)

            def _build_modeler_input(extra_feedback: str = "") -> str:
                base = (
                    f"# 题目结构化信息\n"
                    f"```json\n{json.dumps(questions, ensure_ascii=False, indent=2)}\n```\n\n"
                    f"# 工作区数据文件\n{data_summary}\n\n"
                )
                if data_preview_md:
                    base += data_preview_md + "\n"
                base += (
                    f"题目共 {ques_count} 个问题，请为每个问题制定详细建模方案，"
                    "并包含 EDA 数据分析方案和敏感性分析方案。"
                )
                if extra_feedback:
                    base += f"\n\n# 用户反馈（请据此修改方案）\n{extra_feedback}"
                return base

            modeling_plan = ""
            solutions: dict = {}
            _MAX_REDO = 2         # 最多允许重做 2 次
            _redo_feedback = ""   # 用户反馈（传给下一轮 Modeler）
            plan_path = work_dir / "modeling_plan.md"

            for _redo_round in range(_MAX_REDO + 1):
                modeler = ModelerAgent(task_id=task_id, user_id=uid, tools=None, max_iterations=3)
                _patch_agent_with_hitl(modeler, task_id)
                modeling_plan = await modeler.run(_build_modeler_input(_redo_feedback))
                # 持久化版本文件（v1 起步），便于用户 diff 查看历史
                version_path = work_dir / f"modeling_plan_v{_redo_round + 1}.md"
                version_path.write_text(modeling_plan, encoding="utf-8")
                plan_path.write_text(modeling_plan, encoding="utf-8")
                solutions = parse_modeler_sections(modeling_plan)

                # 已生成的版本文件名列表（v1..vN）
                plan_versions = [
                    f"modeling_plan_v{i + 1}.md"
                    for i in range(_redo_round + 1)
                    if (work_dir / f"modeling_plan_v{i + 1}.md").exists()
                ]

                # HITL：暂停让用户审核方案
                hitl_resp = await task_manager.request_hitl(
                    task_id,
                    f"建模方案已生成（第{_redo_round + 1}稿）。请审核后选择操作：",
                    {
                        "plan": modeling_plan,
                        "plan_preview": modeling_plan[:4000],
                        "sections": list(solutions.keys()),
                        "ques_count": ques_count,
                        "redo_round": _redo_round,
                        "max_redo": _MAX_REDO,
                        "plan_versions": plan_versions,   # 供前端切换查看
                    },
                )
                action = hitl_resp.get("action", "approve")

                if action == "approve":
                    break
                elif action == "edit" and hitl_resp.get("edited_plan", "").strip():
                    modeling_plan = hitl_resp["edited_plan"].strip()
                    plan_path.write_text(modeling_plan, encoding="utf-8")
                    solutions = parse_modeler_sections(modeling_plan)
                    break
                elif action == "redo" and _redo_round < _MAX_REDO:
                    _redo_feedback = hitl_resp.get("feedback", "")
                    logger.info("Modeler redo #{} feedback: {}", _redo_round + 1, _redo_feedback[:80])
                    continue
                else:
                    break

            def _solution_ctx(key: str) -> str:
                """提取指定 key 的建模方案，不存在时降级到全文。"""
                raw_plan = solutions.get(key, "") or solutions.get("_raw", "")
                if raw_plan:
                    return f"# 建模方案（{key}）\n{raw_plan}\n\n"
                return f"# 建模方案（全文）\n{modeling_plan}\n\n"

            data_ctx = f"# 工作区数据文件\n{data_summary}\n\n"
            await task_manager.checkpoint(task_id, "modeler_done", {"sections": list(solutions.keys())})
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
            # 产物对账：Modeler 预期 vs 实际 fig_eda_*.png
            _eda_expected = _extract_expected_figures(
                solutions.get("eda", "") or solutions.get("_raw", ""), scope="eda"
            )
            if _eda_expected:
                await _reconcile_figures(
                    task_id=task_id, user_id=uid, work_dir=work_dir, tools=tools,
                    phase="coder:eda", prefix="fig_eda_", expected=_eda_expected,
                    solution_ctx=_solution_ctx("eda") + data_ctx,
                )
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
                    f"完整实现求解、可视化并保存结果，图表命名：`fig_q{qi}_*.png`。\n\n"
                    f"完成后依次执行：\n"
                    f"1. 用 `write_file` 保存 `result_q{qi}.md`（含关键数值结论和模型评估指标）；\n"
                    f"2. 打印图表产出汇总（**必须**）：\n"
                    f"   ```\n"
                    f"   【图表产出汇总】\n"
                    f"   fig_q{qi}_xxx.png\n"
                    f"   fig_q{qi}_yyy.png\n"
                    f"   （列出本阶段所有 save_fig 保存的文件名）\n"
                    f"   ```\n"
                    f"3. 回复 `TASK_COMPLETE`。"
                )
                try:
                    q_out = await coder_q.run(q_prompt)
                    coder_stdout_log.append(_extract_figure_features(q_out, label=f"问题{qi}"))
                except Exception as _e:
                    logger.warning("coder:q{} failed (non-fatal): {}", qi, _e)
                # 产物对账：fig_q{qi}_*.png
                _q_expected = _extract_expected_figures(
                    solutions.get(ques_key, "") or solutions.get("_raw", ""), scope=f"q{qi}"
                )
                if _q_expected:
                    await _reconcile_figures(
                        task_id=task_id, user_id=uid, work_dir=work_dir, tools=tools,
                        phase=f"coder:q{qi}", prefix=f"fig_q{qi}_", expected=_q_expected,
                        solution_ctx=_solution_ctx(ques_key) + data_ctx + eda_ctx,
                    )
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
            # 产物对账：fig_sens_*.png
            _sens_expected = _extract_expected_figures(
                solutions.get("sensitivity_analysis", "") or solutions.get("_raw", ""),
                scope="sensitivity",
            )
            if _sens_expected:
                await _reconcile_figures(
                    task_id=task_id, user_id=uid, work_dir=work_dir, tools=tools,
                    phase="coder:sensitivity", prefix="fig_sens_", expected=_sens_expected,
                    solution_ctx=_solution_ctx("sensitivity_analysis") + data_ctx,
                )
            await emit(EventType.PHASE_EXIT, task_id, phase="coder:sensitivity")

            await task_manager.checkpoint(task_id, "coder_done", {})
            await emit(EventType.PHASE_EXIT, task_id, phase="coder")

            # ── Phase 3: Writer（分节写作 → 合并）───────────────────────
            # 章节固定编号（对齐参考项目 md_template.toml）：
            #   摘要/标题 一问重 二问析 三假设 四符号+EDA 五建立与求解(5.N) 六敏感 七评价 八参考
            await task_manager.wait_if_paused(task_id)
            await task_manager.update_state(task_id, TaskState.RUNNING, phase="writer")
            await emit(EventType.PHASE_ENTER, task_id, phase="writer")

            # 收集 Coder 产出的报告
            coder_reports = _collect_coder_reports(work_dir, ques_count)
            figure_catalog = _build_figure_catalog(work_dir)
            catalog_text   = _format_catalog_for_writer(figure_catalog)

            # 汇总 Coder 各阶段图表数据特征
            figure_features_text = "\n\n".join(s for s in coder_stdout_log if s.strip())

            # 加载 TOML 模板（各节写作要求）
            tpl = _load_md_template()

            # 公共背景 context（所有节共享）
            bg_questions = "\n".join(
                f"**问题{i}**：{questions.get(f'ques{i}','')}"
                for i in range(1, ques_count + 1)
            )
            model_build_solve = coder_reports  # 给 Writer 看的"模型建立与求解"产出

            # 最终对账：Modeler 全局预期 vs work_dir 实际存在
            all_expected = _extract_expected_figures(modeling_plan, scope="all")
            all_actual = _actual_figures(work_dir)
            final_missing = _diff_missing_figures(all_expected, all_actual)
            missing_section = ""
            if final_missing:
                missing_section = (
                    "# ⚠️ 缺失图表清单（Modeler 方案提及但未成功生成，**严禁引用**）\n"
                    + "\n".join(f"- `{f}`" for f in final_missing)
                    + "\n\n"
                    "**处理规则**：\n"
                    "1. 撰写正文时**禁止使用** `![...](缺失文件名)` 引用上述任何文件；\n"
                    "2. 原本计划在此处插图的段落，改用 1-2 句描述性文字说明该分析结果（可引用 Coder 报告中的数值）；\n"
                    "3. 若整段分析严重依赖缺失图，可缩减该段篇幅，但不得留空或编造图号。\n\n"
                )

            base_ctx = (
                f"# 题目背景\n{questions.get('background', task.problem)}\n\n"
                f"# 各问题描述\n{bg_questions}\n\n"
                f"# 建模方案（Modeler 输出）\n{modeling_plan}\n\n"
                f"# 模型建立与求解（Coder 报告）\n{model_build_solve}\n\n"
                + (f"# 图表数据特征\n{figure_features_text}\n\n" if figure_features_text.strip() else "")
                + f"# ✅ 实际可用图表目录（共 {len(figure_catalog)} 张，仅这些可插入论文）\n{catalog_text}\n\n"
                + missing_section
                + "# 图表引用铁律\n"
                + "- **只允许引用「实际可用图表目录」中列出的文件名**；\n"
                + "- 引用未列出的图片视为严重错误，会导致最终 PDF 断图；\n"
                + "- 若需讨论某分析但无对应图，用文字描述替代，不可编造图号。\n\n"
            )

            # 分节写作辅助
            def _save_section(sec_file: str, content: str) -> str:
                p = work_dir / sec_file
                if not p.exists() or p.stat().st_size < 50:
                    p.write_text(content, encoding="utf-8")
                return p.read_text(encoding="utf-8")

            # OpenAlex email：DB 系统设置优先，回退 env
            openalex_email = settings.openalex_email or ""
            try:
                from ..db import AsyncSessionLocal, SystemSetting
                from sqlalchemy import select as _select
                async with AsyncSessionLocal() as _ss:
                    row = (await _ss.execute(
                        _select(SystemSetting).where(SystemSetting.key == "openalex_email")
                    )).scalar_one_or_none()
                    if row and row.value.strip():
                        openalex_email = row.value.strip()
            except Exception as _e:
                logger.warning("read openalex_email from DB failed (use env): {}", _e)

            writer_tools = build_writer_registry(work_dir, openalex_email=openalex_email)

            async def _write_section(phase: str, prompt: str, sec_file: str) -> str:
                await emit(EventType.PHASE_ENTER, task_id, phase=phase)
                result = ""
                try:
                    w = WriterAgent(task_id=task_id, user_id=uid,
                                   tools=writer_tools, max_iterations=6)
                    _patch_agent_with_hitl(w, task_id)
                    output = await w.run(base_ctx + prompt)
                    result = _save_section(sec_file, output)
                except Exception as _e:
                    logger.warning("writer {} failed (non-fatal): {}", phase, _e)
                await emit(EventType.PHASE_EXIT, task_id, phase=phase)
                await task_manager.wait_if_paused(task_id)
                return result

            sections: list[str] = []

            # ① 摘要 / 标题 / 关键词
            s = await _write_section(
                "writer:abstract",
                tpl.get("firstPage", WRITER_SECTION_ABSTRACT),
                "sec_abstract.md",
            )
            if s: sections.append(s)

            # ② 一、问题重述
            s = await _write_section(
                "writer:restatement",
                tpl.get("RepeatQues", WRITER_SECTION_RESTATEMENT),
                "sec_restatement.md",
            )
            if s: sections.append(s)

            # ③ 二、问题分析
            s = await _write_section(
                "writer:analysis",
                tpl.get("analysisQues", WRITER_SECTION_ANALYSIS),
                "sec_analysis.md",
            )
            if s: sections.append(s)

            # ④ 三、模型假设
            s = await _write_section(
                "writer:assumptions",
                tpl.get("modelAssumption", WRITER_SECTION_ASSUMPTIONS),
                "sec_assumptions.md",
            )
            if s: sections.append(s)

            # ⑤ 四、符号说明（4.1）
            s = await _write_section(
                "writer:symbol",
                tpl.get("symbol", ""),
                "sec_symbol.md",
            )
            if s: sections.append(s)

            # ⑥ 四.2 描述性统计（EDA，附 EDA 图表目录）
            eda_figs = [e for e in figure_catalog if e.get("question") == 0]
            eda_catalog = _format_catalog_for_writer(eda_figs)
            eda_rp = work_dir / "eda_report.md"
            eda_report = ""
            if eda_rp.exists():
                raw = eda_rp.read_text(encoding="utf-8")
                eda_report = raw[:_REPORT_MAX_CHARS] + "\n…（已截断）" if len(raw) > _REPORT_MAX_CHARS else raw
            eda_prompt = (
                tpl.get("eda", WRITER_SECTION_EDA)
                + f"\n\n# EDA 分析报告\n{eda_report}\n\n"
                + f"# EDA 图表目录（必须全部插入）\n{eda_catalog}\n\n"
                + "⚠️ **图片约束**：仅允许引用上方「EDA 图表目录」中列出的文件名；"
                + "目录之外的图片文件名严禁出现在 `![]()` 中，否则生成 PDF 时会断图。"
                + "目录为空时改为用文字描述分析结果，不插入任何图片。\n"
            )
            s = await _write_section("writer:eda", eda_prompt, "sec_eda.md")
            if s: sections.append(s)

            # ⑦ 五、模型的建立与求解（5.1 / 5.2 / 5.N 全部归于同一大章）
            # 第一个问题带 # 五 标题，后续问题只带 ## 5.N 子标题
            for qi in range(1, ques_count + 1):
                phase_name = f"writer:q{qi}"
                # 从 TOML 取对应问题模板（ques1/ques2/...）
                ques_tpl_key = f"ques{qi}"
                ques_tpl = tpl.get(ques_tpl_key, "")
                if not ques_tpl:
                    # 超出 TOML 预设时用通用模板
                    header = "# 五、模型的建立与求解\n\n" if qi == 1 else ""
                    ques_tpl = (
                        f"{header}## 5.{qi} 问题{qi}模型的建立与求解\n\n"
                        f"### 5.{qi}.1 模型的建立\n"
                        f"### 5.{qi}.2 模型的求解\n约600字实质内容。"
                    )

                ques_figs = [e for e in figure_catalog if e.get("question") == qi]
                ques_catalog = _format_catalog_for_writer(ques_figs)
                ques_result = ""
                rp = work_dir / f"result_q{qi}.md"
                if rp.exists():
                    raw = rp.read_text(encoding="utf-8")
                    ques_result = raw[:_REPORT_MAX_CHARS] + "\n…（已截断）" if len(raw) > _REPORT_MAX_CHARS else raw

                q_prompt = "\n".join([
                    ques_tpl,
                    "",
                    f"### 问题{qi}描述",
                    questions.get(f"ques{qi}", ""),
                    "",
                    f"### Coder 求解报告（问题{qi}，含全部关键数值）",
                    ques_result or "（无报告，依据建模方案撰写）",
                    "",
                    f"### 本问图表目录（必须全部插入，一张不得遗漏）",
                    ques_catalog or "（本问暂无图表，以文字描述结果）",
                    "",
                    "### 写作约束（必须全部满足）",
                    f"- 正文不少于600字实质内容（不含图表标注）；",
                    f"- 每个公式独立成行 $$...$$，变量首次出现时定义含义；",
                    f"- 每张图前2-3句铺垫（含具体数值），图后1-2句结论；",
                    f"- 图表格式：![描述](文件名)\\n**图X：说明**；",
                    f"- 所有数值来自 Coder 报告，精确4位有效数字，严禁编造；",
                    f"- 全文禁止 bullet 列举，段落式叙述；",
                    f"- ⚠️ **仅允许引用「本问图表目录」中的文件名**，目录外路径严禁出现在 `![]()` 中；",
                    f"  目录为空或某图缺失时，改用文字描述该分析结论，不可编造图号。",
                ])
                s = await _write_section(phase_name, q_prompt, f"sec_q{qi}.md")
                if s: sections.append(s)

            # ⑧ 六、模型的分析与检验（敏感性分析，固定为第六章）
            sens_p = work_dir / "sensitivity_report.md"
            sens_report = ""
            if sens_p.exists():
                raw = sens_p.read_text(encoding="utf-8")
                sens_report = raw[:_REPORT_MAX_CHARS] + "\n…（已截断）" if len(raw) > _REPORT_MAX_CHARS else raw
            sens_figs = [e for e in figure_catalog if e.get("question") == -1]
            sens_catalog = _format_catalog_for_writer(sens_figs)
            sens_prompt = (
                tpl.get("sensitivity_analysis", WRITER_SECTION_SENSITIVITY)
                + f"\n\n# 敏感性分析报告\n{sens_report}\n\n"
                + f"# 敏感性图表目录（必须全部插入）\n{sens_catalog}\n\n"
                + "⚠️ **图片约束**：仅允许引用上方「敏感性图表目录」中列出的文件名；"
                + "目录之外的图片路径严禁出现在 `![]()` 中。目录为空时改用文字描述敏感性结论。\n"
            )
            s = await _write_section("writer:sensitivity", sens_prompt, "sec_sensitivity.md")
            if s: sections.append(s)

            # ① 模型评价 + 八、参考文献（固定为第七章）
            s = await _write_section(
                "writer:evaluation",
                tpl.get("judge", WRITER_SECTION_EVALUATION),
                "sec_evaluation.md",
            )
            if s: sections.append(s)

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
        # async with JupyterSandbox 退出时 sandbox 自动关闭
        task_manager.unregister_sandbox(task_id)

    except asyncio.CancelledError:
        task_manager.unregister_sandbox(task_id)
        logger.info("workflow cancelled | task={}", task_id)
        raise
    except Exception as e:
        task_manager.unregister_sandbox(task_id)
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


_REPORT_MAX_CHARS = 10_000  # 单个 Coder 报告截断阈值，防 context 溢出


def _collect_coder_reports(work_dir: Path, ques_count: int) -> str:
    """收集 Coder 各阶段产出的报告文件，合并为字符串（单文件超限时截断）。"""
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
            if len(content) > _REPORT_MAX_CHARS:
                content = content[:_REPORT_MAX_CHARS] + "\n…（报告过长已截断）"
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
        q = e["question"]
        if q == 0:
            qstr = "[EDA]"
        elif q == -1:
            qstr = "[敏感性]"
        else:
            qstr = f"[问题{q}]"
        desc = f" — {e['desc']}" if e.get("desc") else ""
        lines.append(
            f"- 图{e['index']} {qstr} `{e['file']}` caption=\"{e['caption']}\"{desc}"
        )
    return "\n".join(lines)


_FIG_NAME_RE = re.compile(r"fig_[a-zA-Z0-9_]+\.(?:png|jpg|jpeg|svg|webp)", re.IGNORECASE)


async def _reconcile_figures(
    *, task_id: str, user_id: int, work_dir: Path, tools,
    phase: str, prefix: str, expected: set[str],
    solution_ctx: str, max_iterations: int = 4,
) -> None:
    """对比 Modeler 预期图表与 Coder 实际产出，缺失时发事件并让 Coder 一次性补做。

    只补做一次，避免陷入循环；补做失败也只记 warning。
    """
    actual = _actual_figures(work_dir, prefix=prefix)
    missing = _diff_missing_figures(expected, actual)
    if not missing:
        return

    await emit(EventType.ARTIFACT_MISSING, task_id,
               phase=phase, expected=sorted(expected),
               actual=sorted(actual), missing=missing)
    logger.warning("[{}] 图表缺失 {} 张：{}", phase, len(missing), missing)

    # 一次性补做 prompt：只让 Coder 补生成缺失的图表
    fix_prompt = (
        solution_ctx +
        f"\n\n## ⚠️ 补做任务（{phase}）\n\n"
        f"先前执行未生成以下图表，请**仅补生成这些缺失文件**，不要重复已有工作：\n\n"
        + "\n".join(f"- `{f}`" for f in missing) +
        "\n\n要求：\n"
        "1. 使用既有数据和模型结果（可从 result_q*.md / eda_report.md 读取）；\n"
        "2. 图表风格遵守全局约束（std_fig / save_fig / PALETTE）；\n"
        "3. 完成后打印 `[saved] <文件名>` 并回复 `TASK_COMPLETE`；\n"
        "4. 如某图确实无法合理生成（如依赖的模型失败），请用 print 说明原因并跳过。"
    )
    try:
        from ..agents import CoderAgent as _CoderAgent
        fix_agent = _CoderAgent(
            task_id=task_id, user_id=user_id, tools=tools,
            max_iterations=max_iterations,
        )
        _patch_agent_with_hitl(fix_agent, task_id)  # 保证暂停/取消可中断
        await fix_agent.run(fix_prompt)
    except Exception as e:
        logger.warning("[{}] 补做失败（非致命）: {}", phase, e)

    # 补做后重新对账，发 recovered 事件
    actual2 = _actual_figures(work_dir, prefix=prefix)
    still_missing = _diff_missing_figures(expected, actual2)
    recovered = sorted(set(missing) - set(still_missing))
    if recovered:
        await emit(EventType.ARTIFACT_RECOVERED, task_id,
                   phase=phase, recovered=recovered, still_missing=still_missing)
        logger.info("[{}] 补做成功 {} 张，仍缺 {} 张", phase, len(recovered), len(still_missing))


def _extract_expected_figures(plan_text: str, scope: str = "all") -> set[str]:
    """从 Modeler 方案正文中提取预期图表文件名集合。

    scope:
      - "eda"          仅 fig_eda_*
      - "q{N}"         仅 fig_q{N}_*
      - "sensitivity"  仅 fig_sens_*
      - "all"          全部
    """
    if not plan_text:
        return set()
    names = set(m.group(0).lower() for m in _FIG_NAME_RE.finditer(plan_text))
    if scope == "all":
        return names
    if scope == "eda":
        return {n for n in names if n.startswith("fig_eda")}
    if scope == "sensitivity":
        return {n for n in names if n.startswith("fig_sens")}
    if scope.startswith("q"):
        prefix = f"fig_{scope}_"
        return {n for n in names if n.startswith(prefix)}
    return set()


def _actual_figures(work_dir: Path, prefix: str = "") -> set[str]:
    """扫描工作区实际存在的图片（小写文件名），可选按前缀过滤。"""
    result = set()
    for p in work_dir.iterdir():
        if p.is_file() and p.suffix.lower() in _IMAGE_EXTS:
            name = p.name.lower()
            if prefix and not name.startswith(prefix):
                continue
            result.add(name)
    return result


def _diff_missing_figures(expected: set[str], actual: set[str]) -> list[str]:
    """返回 Modeler 预期但 Coder 未产出的图片文件名（排序）。"""
    return sorted(expected - actual)


def _load_paper_template() -> str:
    """加载 config/paper_template.md，失败则返回空字符串。"""
    try:
        p = Path(__file__).parent.parent / "config" / "paper_template.md"
        if p.exists():
            return p.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("paper_template.md 加载失败: {}", e)
    return ""


def _load_md_template() -> dict[str, str]:
    """加载 config/md_template.toml。
    返回 dict[section_key, prompt_str]，失败时返回空字典（各节将降级用 prompts.py 常选）。
    """
    try:
        import tomllib  # Python 3.11+
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore
        except ImportError:
            logger.warning("tomllib/tomli 不可用，跳过 TOML 模板加载")
            return {}
    try:
        p = Path(__file__).parent.parent / "config" / "md_template.toml"
        if p.exists():
            with open(p, "rb") as f:
                data = tomllib.load(f)
            return {k: v for k, v in data.items() if isinstance(v, str)}
    except Exception as e:
        logger.warning("md_template.toml 加载失败: {}", e)
    return {}


def _extract_figure_features(agent_output: str, label: str = "") -> str:
    """从 Agent 返回文本中提取以下内容，供 Writer 使用：
    - 【图数据特征 - *】 块
    - 【建模结果汇总】 块
    - 【图表产出汇总】 块（Coder 打印的已保存文件清单）
    若无任何内容则返回空字符串。
    """
    if not agent_output:
        return ""
    import re as _re
    # 匹配 【图数据特征/建模结果汇总/图表产出汇总】 开头的块（多行，直到下一个空行或文末）
    pattern = _re.compile(
        r"(【(?:图数据特征|建模结果汇总|图表产出汇总)[^\n]*】.*?)(?=\n\n|\Z)",
        _re.DOTALL,
    )
    matches = pattern.findall(agent_output)
    if not matches:
        return ""
    prefix = f"## {label}\n" if label else ""
    return prefix + "\n\n".join(m.strip() for m in matches)


def _ensure_all_figures_in_paper(paper_md: Path, catalog: list[dict]) -> None:
    """后处理：把 catalog 中未被 paper.md 引用的图就近追加，避免遗漏断图。

    追加策略（按归属章节）：
    - question==0  (EDA)  → 追加至「描述性统计」章节尾部
    - question==N  (各问) → 追加至对应「5.N」子节尾部
    - question==-1 (敏感性) → 追加至「六」章节尾部
    - 兜底 → 追加至全文末尾
    """
    content = paper_md.read_text(encoding="utf-8")
    missing_entries = [e for e in catalog if e["file"] not in content]
    if not missing_entries:
        return

    logger.warning("paper.md 遗漏 {} 张图，尝试就近追加", len(missing_entries))

    # 按问题分组
    groups: dict[int, list[dict]] = defaultdict(list)
    for e in missing_entries:
        groups[e["question"]].append(e)

    lines = content.split("\n")
    insertions: list[tuple[int, str]] = []   # (line_index_after, text_to_insert)

    def _find_section_end(keyword_patterns: list[str]) -> int:
        """找到包含关键词的最后一行，返回其行号（0-based），找不到返回 -1。"""
        last = -1
        for i, line in enumerate(lines):
            for kw in keyword_patterns:
                if kw in line:
                    last = i
        return last

    for qn, entries in sorted(groups.items()):
        # 找插入点
        if qn == 0:
            # EDA 章节：找"描述性统计"或"EDA"相关标题
            pos = _find_section_end(["描述性统计", "EDA", "数据预处理", "四.2", "4.2"])
        elif qn == -1:
            pos = _find_section_end(["六、", "敏感性分析", "6.1", "## 六"])
        else:
            pos = _find_section_end([f"5.{qn}", f"5.{qn}.", f"问题{qn}模型", f"## 5.{qn}"])

        snippet = "\n"
        for e in entries:
            snippet += (
                f"\n如图{e['index']}所示，{e['caption']}的分析结果如下。\n\n"
                f"![{e['caption']}]({e['file']})\n\n"
                f"**图{e['index']}：{e['caption']}**\n"
            )

        if pos >= 0:
            insertions.append((pos, snippet))
        else:
            # 兜底：追加至文末
            insertions.append((len(lines) - 1, snippet))

    # 从后向前插入（避免行号偏移）
    for pos, text in sorted(insertions, key=lambda x: x[0], reverse=True):
        lines.insert(pos + 1, text)

    paper_md.write_text("\n".join(lines), encoding="utf-8")
