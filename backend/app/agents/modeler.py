from __future__ import annotations

import re

from ..core.logging import logger
from .base import BaseAgent
from .prompts import MODELER_SYSTEM

# 节标题到 dict key 的映射规则
_SECTION_ALIASES: dict[str, str] = {
    "eda": "eda",
    "数据分析": "eda",
    "描述性统计": "eda",
    "敏感性": "sensitivity_analysis",
    "sensitivity": "sensitivity_analysis",
    "灵敏度": "sensitivity_analysis",
}


def parse_modeler_sections(text: str) -> dict[str, str]:
    """
    解析 Modeler 输出的结构化 Markdown，提取各 ## 节内容。
    返回 dict，key 为 eda / ques1 / ques2 / ... / sensitivity_analysis。
    解析失败时返回 {"_raw": text}，供 orchestrator 降级使用全文。
    """
    result: dict[str, str] = {}
    # 按 ## 分割（保留标题行）
    parts = re.split(r"(?m)^(## .+)$", text)
    # parts = [preamble, '## 标题', 内容, '## 标题', 内容, ...]
    i = 1
    while i < len(parts) - 1:
        header = parts[i].strip()       # e.g. "## EDA 数据分析方案"
        content = parts[i + 1].strip()  # 该节全文
        i += 2

        title = re.sub(r"^##\s*", "", header).strip().lower()

        # 尝试匹配内置别名
        key = next((v for k, v in _SECTION_ALIASES.items() if k in title), None)
        if key is None:
            # 匹配 "问题N"
            m = re.search(r"问题\s*(\d+)", title)
            if m:
                key = f"ques{m.group(1)}"

        if key:
            result[key] = content
            logger.debug("ModelerAgent section: {} → {}", header, key)
        else:
            logger.debug("ModelerAgent: unrecognized section header '{}'", header)

    if not result:
        logger.warning("ModelerAgent: no sections parsed, using raw fallback")
        return {"_raw": text}

    logger.info("ModelerAgent sections: {}", list(result.keys()))
    return result


class ModelerAgent(BaseAgent):
    name = "modeler"
    system_prompt = MODELER_SYSTEM
    max_memory: int = 20

    async def run(self, user_input: str, images=None) -> str:  # type: ignore[override]
        """运行 Modeler，返回原始 Markdown 方案文本（供 orchestrator 解析 + HITL 展示）。"""
        raw = await super().run(user_input, images)
        # 验证是否包含预期节标题，输出日志便于调试
        sections = parse_modeler_sections(raw)
        logger.info("ModelerAgent finished: sections={}", list(sections.keys()))
        return raw
