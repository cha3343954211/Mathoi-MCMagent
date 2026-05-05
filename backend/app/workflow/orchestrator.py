"""主工作流：Modeler -> Coder -> Writer。所有 Agent 调用都带 user_id，自动按用户配置 + 计量。"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..agents import CoderAgent, ModelerAgent, WriterAgent
from ..core.config import get_settings
from ..core.events import EventType, emit
from ..core.logging import logger
from ..exporters import export_paper
from ..sandbox import JupyterSandbox
from ..tasks import TaskState, task_manager
from ..tools import build_default_registry

# 工作区内被识别为图片的扩展名
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".svg", ".webp"}


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

            # ── Phase 1: Modeler ──────────────────────────────────────────
            await task_manager.update_state(task_id, TaskState.RUNNING, phase="modeler")
            await emit(EventType.PHASE_ENTER, task_id, phase="modeler")

            data_summary = _describe_data(work_dir, task.data_files)
            modeler_input = (
                f"# 赛题\n{task.problem}\n\n"
                f"# 工作区数据文件\n{data_summary}\n\n"
                "请输出完整结构化的建模方案（Markdown）。"
            )
            modeler = ModelerAgent(task_id=task_id, user_id=uid, tools=None, max_iterations=2)
            _patch_agent_with_hitl(modeler, task_id)
            modeling_plan = await modeler.run(modeler_input)
            (work_dir / "modeling_plan.md").write_text(modeling_plan, encoding="utf-8")
            await task_manager.checkpoint(task_id, "modeler_done", {"plan": modeling_plan})
            await emit(EventType.PHASE_EXIT, task_id, phase="modeler")

            # HITL
            hitl = await task_manager.request_hitl(
                task_id,
                prompt="请审查建模方案，可直接通过 / 修改后通过 / 让 Modeler 重做。",
                context={"plan_preview": modeling_plan[:1500]},
            )
            action = (hitl or {}).get("action", "approve")
            if action == "edit":
                modeling_plan = hitl.get("edited_plan", modeling_plan)
                (work_dir / "modeling_plan.md").write_text(modeling_plan, encoding="utf-8")
            elif action == "redo":
                feedback = hitl.get("feedback", "请重新优化")
                modeling_plan = await modeler.run(f"用户反馈：{feedback}\n请重新输出建模方案。")
                (work_dir / "modeling_plan.md").write_text(modeling_plan, encoding="utf-8")

            # ── Phase 2: Coder ────────────────────────────────────────────
            await task_manager.update_state(task_id, TaskState.RUNNING, phase="coder")
            await emit(EventType.PHASE_ENTER, task_id, phase="coder")
            coder_input = (
                f"# 建模方案\n{modeling_plan}\n\n"
                f"# 工作区数据文件\n{data_summary}\n\n"
                "请按方案逐问实现，每张图用 fig_q{{问题编号}}_{{描述}}.png 命名并 savefig；"
                "最后用 write_file 保存 analysis_report.md（末尾含 ## 图表目录 JSON 块），再回复 TASK_COMPLETE。"
            )
            coder = CoderAgent(task_id=task_id, user_id=uid, tools=tools,
                               max_iterations=settings.max_coder_iterations)
            _patch_agent_with_hitl(coder, task_id)
            coder_summary = await coder.run(coder_input)
            await task_manager.checkpoint(task_id, "coder_done", {"summary": coder_summary})
            await emit(EventType.PHASE_EXIT, task_id, phase="coder")

            # ── Phase 3: Writer ───────────────────────────────────────────
            await task_manager.update_state(task_id, TaskState.RUNNING, phase="writer")
            await emit(EventType.PHASE_ENTER, task_id, phase="writer")

            analysis_report = ""
            arp = work_dir / "analysis_report.md"
            if arp.exists():
                analysis_report = arp.read_text(encoding="utf-8")

            # 构建结构化图表目录
            figure_catalog = _build_figure_catalog(work_dir, analysis_report)
            catalog_text   = _format_catalog_for_writer(figure_catalog)

            writer_input = (
                f"# 题目\n{task.problem}\n\n"
                f"# 建模方案\n{modeling_plan}\n\n"
                f"# Coder 分析报告\n{analysis_report or coder_summary}\n\n"
                f"# 图表目录（共 {len(figure_catalog)} 张，每张均须插入论文）\n"
                f"{catalog_text}\n\n"
                "请撰写完整论文并 write_file 保存到 paper.md。"
                "每问至少插入一张图，图前后须有分析文字，图题格式：**图N：caption**。"
            )
            writer = WriterAgent(task_id=task_id, user_id=uid, tools=tools, max_iterations=6)
            _patch_agent_with_hitl(writer, task_id)
            await writer.run(writer_input)
            await emit(EventType.PHASE_EXIT, task_id, phase="writer")

            # 后处理：补充 catalog 中未被引用的图片到论文末尾
            paper_md = work_dir / "paper.md"
            if paper_md.exists() and figure_catalog:
                _ensure_all_figures_in_paper(paper_md, figure_catalog)
                docx_path = export_paper(paper_md, work_dir / "paper.docx")
                logger.info("Paper exported: {}", docx_path)

            await task_manager.update_state(task_id, TaskState.COMPLETED, phase="done")
            await emit(EventType.TASK_COMPLETED, task_id)

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


def _build_figure_catalog(work_dir: Path, analysis_report: str) -> list[dict]:
    """
    1. 优先解析 analysis_report 末尾的 ```json 图表目录块；
    2. 再扫描工作区所有图片文件补充未列出的图；
    3. 返回 list[{index, file, question, caption, desc}]。
    """
    # Step 1：尝试从 analysis_report 解析 JSON 图表目录
    parsed: list[dict] = []
    m = re.search(r"```json\s*(\[.*?\])\s*```", analysis_report, re.DOTALL)
    if m:
        try:
            raw = json.loads(m.group(1))
            for item in raw:
                if isinstance(item, dict) and "file" in item:
                    parsed.append({
                        "file":     item.get("file", ""),
                        "question": int(item.get("question", 0)),
                        "caption":  item.get("caption", ""),
                        "desc":     item.get("desc", ""),
                    })
        except (json.JSONDecodeError, ValueError):
            pass

    # Step 2：扫描工作区图片（跳过 data_files 等非分析图）
    existing_files = {
        p.name for p in work_dir.iterdir()
        if p.is_file() and p.suffix.lower() in _IMAGE_EXTS
    }
    parsed_files = {e["file"] for e in parsed}

    # 已解析中不存在的图（Coder 命名不规范时）
    for fname in sorted(existing_files - parsed_files):
        # 猜测问题编号：fig_q1_xxx.png → 1
        qn = 0
        qm = re.match(r"fig_q(\d+)", fname)
        if qm:
            qn = int(qm.group(1))
        parsed.append({
            "file":     fname,
            "question": qn,
            "caption":  fname.rsplit(".", 1)[0].replace("_", " "),
            "desc":     "",
        })

    # 过滤掉工作区不存在的文件
    catalog = [e for e in parsed if e["file"] in existing_files]

    # 按问题编号 + 文件名排序，分配连续编号
    catalog.sort(key=lambda e: (e["question"], e["file"]))
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
        desc = f" — {e['desc']}" if e["desc"] else ""
        lines.append(
            f"- 图{e['index']} {qstr} `{e['file']}` caption=\"{e['caption']}\"{desc}"
        )
    return "\n".join(lines)


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
