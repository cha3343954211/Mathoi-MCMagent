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

    # 摘要式压缩的触发阈值：超过 max_memory 的能加入摘要的消息数。
    # 小于该值时直接返回，避免为短对话浪费额外 LLM 调用。
    _SUMMARIZE_THRESHOLD: int = 6

    def _trim_history(self) -> None:
        """滑窗压缩（化简兼容层）：超过 max_memory 时保留 system + 最近若干条。
        tool 调用对必须整体保留，避免破坏 assistant/tool 配对。"""
        if len(self.history) <= self.max_memory:
            return
        system_msgs = [m for m in self.history if m.role == "system"]
        non_system = [m for m in self.history if m.role != "system"]
        keep = non_system[-(self.max_memory - len(system_msgs)):]
        while keep and keep[0].role == "tool":
            idx = len(non_system) - len(keep) - 1
            if idx >= 0:
                keep = [non_system[idx]] + keep
            else:
                break
        self.history = system_msgs + keep
        logger.debug("Agent {} history trimmed to {} msgs", self.name, len(self.history))

    def _safe_cut_point(self, non_system: list[ChatMessage], keep_n: int) -> int:
        """返回安全的分割下标：non_system[cut:] 不以 tool 消息开头。"""
        cut = max(0, len(non_system) - keep_n)
        # 如果切割点落在 tool 消息上，往前找到配对的 assistant
        while cut > 0 and non_system[cut].role == "tool":
            cut -= 1
        return cut

    async def _maybe_summarize_history(self) -> None:
        """历史压缩：优先用 LLM 对早期对话做语义摘要。

        流程：
        1. 少于阈值时返回（无操作）
        2. 分拜“待摘要消息”（较早的）和“保留消息”（最近的）
        3. 尝试 LLM 摘要，成功则用摘要消息替换早期对话
        4. LLM 失败时回退到 _trim_history（滑窗）
        """
        if len(self.history) <= self.max_memory:
            return

        system_msgs = [m for m in self.history if m.role == "system"]
        non_system  = [m for m in self.history if m.role != "system"]

        # 保留最近 keep_n 条，摘要剩下的
        keep_n = max(self.max_memory // 2, 4)
        cut = self._safe_cut_point(non_system, keep_n)
        to_summarize = non_system[:cut]
        to_keep      = non_system[cut:]

        # 待摘要的消息条数要达到阈值，否则滑窗即可
        if len(to_summarize) < self._SUMMARIZE_THRESHOLD:
            self._trim_history()
            return

        # 拼接待摘要消息的文本表示（裁剪过长内容）
        MAX_CONTENT = 800
        lines: list[str] = []
        for m in to_summarize:
            role = m.role
            if role == "tool":
                raw = m.content or ""
                snippet = raw[:MAX_CONTENT] + ("…" if len(raw) > MAX_CONTENT else "")
                lines.append(f"[tool返回] {snippet}")
            elif role == "assistant" and m.tool_calls:
                calls = ", ".join(
                    c.get("function", {}).get("name", "") for c in m.tool_calls
                )
                lines.append(f"[assistant调用工具: {calls}]")
            else:
                raw = m.content or ""
                snippet = raw[:MAX_CONTENT] + ("…" if len(raw) > MAX_CONTENT else "")
                lines.append(f"[{role}] {snippet}")
        history_text = "\n".join(lines)

        summarize_prompt = (
            "请用 3–5 句话简洁总结以下对话中已完成的工作和关键结论。"
            "保留：数值结果、模型选择理由、重要变量名、文件名、错误类型及修复方式。"
            "不要包含任何引言、多余解释或超出总结的内容。\n\n"
            f"对话内容：\n{history_text}"
        )
        summarize_msgs = [
            *system_msgs,
            ChatMessage(role="user", content=summarize_prompt),
        ]

        try:
            summary_msg = await chat_for_user(
                user_id=self.user_id,
                agent=self.name,
                messages=summarize_msgs,
                task_id=self.task_id,
                tools=None,       # 摘要时不调工具
                max_tokens=512,   # 摘要不要太长
            )
            summary_text = summary_msg.content or ""
            # 用摘要消息替换早期对话
            summary_holder = ChatMessage(
                role="assistant",
                content=f"[历史摘要 — 覆盖第 1–{cut} 条消息]\n{summary_text}",
            )
            self.history = system_msgs + [summary_holder] + to_keep
            logger.info(
                "Agent {} history summarized: {} msgs → {} (summary+keep)",
                self.name, len(non_system), 1 + len(to_keep),
            )
        except Exception as exc:
            logger.warning(
                "Agent {} LLM summarize failed ({}), fallback to trim",
                self.name, exc,
            )
            self._trim_history()

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

            await self._maybe_summarize_history()
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
