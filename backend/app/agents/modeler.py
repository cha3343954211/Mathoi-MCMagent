from __future__ import annotations

import json
import re

from ..core.logging import logger
from .base import BaseAgent
from .prompts import MODELER_SYSTEM


def _repair_modeler_json(text: str) -> dict | None:
    """多策略解析 Modeler 的 JSON 输出。"""
    text = re.sub(r"```json\s*", "", text)
    text = re.sub(r"```\s*", "", text).strip()
    # 策略1：直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 策略2：提取 {...} 块
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    # 策略3：修复未转义换行
    try:
        fixed = re.sub(
            r'(?<=: ")(.*?)(?=",\s*\n\s*"|"\s*\n\s*})',
            lambda mo: mo.group(0).replace('"', '\\"').replace('\n', '\\n'),
            text, flags=re.DOTALL,
        )
        return json.loads(fixed)
    except Exception:
        pass
    # 策略4：正则暴力提取 key-value
    try:
        pairs = re.findall(r'"(eda|ques\d+|sensitivity_analysis)"\s*:\s*"((?:[^"\\]|\\.)*)"', text, re.DOTALL)
        if pairs:
            return {k: v.replace('\\"', '"') for k, v in pairs}
    except Exception:
        pass
    return None


class ModelerAgent(BaseAgent):
    name = "modeler"
    system_prompt = MODELER_SYSTEM
    max_memory: int = 20   # Modeler 单次调用，不需要长历史

    async def run(self, user_input: str, images=None) -> str:  # type: ignore[override]
        """运行 Modeler，返回 JSON 字符串（供 orchestrator 解析为 dict）。
        即使 LLM 输出格式不对，也会尽力修复；最终失败则返回原始文本。"""
        raw = await super().run(user_input, images)
        parsed = _repair_modeler_json(raw)
        if parsed:
            logger.info("ModelerAgent: parsed JSON with keys={}", list(parsed.keys()))
            return json.dumps(parsed, ensure_ascii=False)
        logger.warning("ModelerAgent: JSON parse failed, returning raw text")
        return raw
