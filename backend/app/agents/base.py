"""BaseAgent：统一的 LLM + 工具循环。每次调用自动按用户配置解析并计量。"""
from __future__ import annotations

import json
from typing import Optional

from ..core.events import EventType, emit
from ..core.logging import logger
from ..llm import ChatMessage, ContentPart, chat_for_user
from ..tools import ToolRegistry


class BaseAgent:
    name: str = "base"
    system_prompt: str = ""
    max_memory: int = 40   # 保留最近 N 条消息（含 system），防 context 超限

    def __init__(
        self,
        task_id: str,
        user_id: int,
        tools: Optional[ToolRegistry] = None,
        max_iterations: int = 10,
    ) -> None:
        self.task_id = task_id
        self.user_id = user_id
        self.tools = tools
        self.max_iterations = max_iterations
        self.history: list[ChatMessage] = []
        if self.system_prompt:
            self.history.append(ChatMessage(role="system", content=self.system_prompt))

    async def wait_if_paused(self) -> None:
        pass

    def _trim_history(self) -> None:
        """滑窗压缩：超过 max_memory 时保留 system 消息 + 最近 (max_memory-1) 条。
        tool 调用对必须整体保留，避免破坏 assistant/tool 配对导致 API 报错。"""
        if len(self.history) <= self.max_memory:
            return
        system_msgs = [m for m in self.history if m.role == "system"]
        non_system = [m for m in self.history if m.role != "system"]
        keep = non_system[-(self.max_memory - len(system_msgs)):]
        # 若 keep 第一条是 tool 消息，往前多保留直到找到对应 assistant 调用
        while keep and keep[0].role == "tool":
            idx = len(non_system) - len(keep) - 1
            if idx >= 0:
                keep = [non_system[idx]] + keep
            else:
                break
        self.history = system_msgs + keep
        logger.debug("Agent {} history trimmed to {} msgs", self.name, len(self.history))

    async def _chat_with_retry(self, tool_specs) -> ChatMessage:
        """LLM 调用（tenacity 内部已处理重试，这里不再叠加）。"""
        return await chat_for_user(
            user_id=self.user_id,
            agent=self.name,
            messages=self.history,
            task_id=self.task_id,
            tools=tool_specs,
        )

    async def run(
        self,
        user_input: str,
        images: Optional[list[ContentPart]] = None,
    ) -> str:
        """执行 Agent。images 为可选的图片内容块（image_url 格式）。"""
        msg = ChatMessage.user(user_input, images)
        self.history.append(msg)
        await emit(EventType.AGENT_MESSAGE, self.task_id, agent=self.name,
                   role="user", content=user_input,
                   has_images=bool(images), image_count=len(images) if images else 0)

        for step in range(self.max_iterations):
            await self.wait_if_paused()
            await emit(EventType.AGENT_THINKING, self.task_id, agent=self.name, step=step + 1)

            self._trim_history()
            tool_specs = self.tools.specs() if self.tools else None
            try:
                msg = await self._chat_with_retry(tool_specs)
            except Exception as e:
                logger.exception("LLM error after retries")
                await emit(EventType.AGENT_MESSAGE, self.task_id, agent=self.name,
                           role="assistant", content=f"[LLM error] {e}")
                raise

            self.history.append(msg)
            await emit(
                EventType.AGENT_MESSAGE, self.task_id, agent=self.name,
                role="assistant", content=msg.content, has_tools=bool(msg.tool_calls),
            )

            if not msg.tool_calls:
                return msg.content

            for call in msg.tool_calls:
                fn = call.get("function", {})
                name = fn.get("name", "")
                raw_args = fn.get("arguments", "{}")
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except json.JSONDecodeError:
                    args = {}
                call_id = call.get("id", "")

                await emit(
                    EventType.AGENT_TOOL_CALL, self.task_id, agent=self.name,
                    tool=name, args=args, call_id=call_id,
                )

                tool = self.tools.get(name) if self.tools else None
                if tool is None:
                    result = {"success": False, "error": f"unknown tool: {name}"}
                else:
                    try:
                        result = await tool.invoke(**args)
                    except Exception as e:
                        logger.exception("tool {} failed", name)
                        result = {"success": False, "error": str(e)}

                result_text = json.dumps(result, ensure_ascii=False, default=str)
                if len(result_text) > 6000:
                    result_text = result_text[:6000] + f' ... "(truncated, total {len(result_text)})"'

                await emit(
                    EventType.AGENT_TOOL_RESULT, self.task_id, agent=self.name,
                    tool=name, result=result, call_id=call_id,
                )

                self.history.append(ChatMessage(
                    role="tool",
                    content=result_text,
                    tool_call_id=call_id,
                    name=name,
                ))

        logger.warning("Agent {} reached max_iterations", self.name)
        return self.history[-1].content if self.history else ""
