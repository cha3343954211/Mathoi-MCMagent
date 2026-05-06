"""CoordinatorAgent：一次性 LLM 调用，将问题文本结构化为 JSON。"""
from __future__ import annotations

import json
import re

from ..core.events import EventType, emit
from ..core.logging import logger
from typing import Optional
from ..llm import ChatMessage, ContentPart, chat_for_user
from .prompts import COORDINATOR_SYSTEM


class CoordinatorAgent:
    """不使用工具循环，单次 LLM 调用 + JSON 解析 + 最多3次重试。"""

    name = "coordinator"
    MAX_RETRIES = 3

    def __init__(self, task_id: str, user_id: int) -> None:
        self.task_id = task_id
        self.user_id = user_id

    async def run(
        self,
        problem_text: str,
        images: Optional[list[ContentPart]] = None,
    ) -> dict:
        """
        输入题目原文（可附图片），返回结构化 dict：
        { title, background, ques_count, ques1, ques2, ... }
        """
        await emit(EventType.AGENT_MESSAGE, self.task_id,
                   agent=self.name, role="user", content=problem_text,
                   has_images=bool(images), image_count=len(images) if images else 0)

        history: list[ChatMessage] = [
            ChatMessage(role="system", content=COORDINATOR_SYSTEM),
            ChatMessage.user(problem_text, images),
        ]

        for attempt in range(1, self.MAX_RETRIES + 1):
            await emit(EventType.AGENT_THINKING, self.task_id,
                       agent=self.name, step=attempt)
            try:
                msg = await chat_for_user(
                    user_id=self.user_id,
                    agent=self.name,
                    messages=history,
                    task_id=self.task_id,
                    tools=None,
                )
            except Exception as e:
                logger.exception("CoordinatorAgent LLM error attempt {}", attempt)
                raise

            raw = msg.content or ""
            await emit(EventType.AGENT_MESSAGE, self.task_id,
                       agent=self.name, role="assistant", content=raw)

            questions = _parse_json(raw)
            if questions and "ques_count" in questions:
                logger.info("CoordinatorAgent parsed questions: ques_count={}",
                            questions.get("ques_count"))
                return questions

            # 解析失败，追加错误提示让 LLM 重试
            logger.warning("CoordinatorAgent JSON parse failed attempt {}/{}", attempt, self.MAX_RETRIES)
            history.append(ChatMessage(role="assistant", content=raw))
            history.append(ChatMessage(
                role="user",
                content=(
                    "⚠️ 上次输出格式有误，请严格按 JSON 格式重新输出，"
                    "只输出 JSON 对象，不要有其他文字。"
                ),
            ))

        # 兜底：无法解析时返回最简结构
        logger.error("CoordinatorAgent failed after {} retries, using fallback", self.MAX_RETRIES)
        return {
            "title": "数学建模题目",
            "background": problem_text,
            "ques_count": 1,
            "ques1": problem_text,
        }


def _parse_json(text: str) -> dict | None:
    """尝试从 LLM 输出中提取 JSON 对象。"""
    # 去除 markdown 代码块
    text = re.sub(r"```json\s*", "", text)
    text = re.sub(r"```\s*", "", text)
    text = text.strip()

    # 直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 提取第一个 {...} 块
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass

    return None
