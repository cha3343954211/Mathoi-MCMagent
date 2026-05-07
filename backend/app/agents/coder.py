from __future__ import annotations

import json

from ..core.events import EventType, emit
from ..core.logging import logger
from ..llm import ChatMessage
from .base import BaseAgent
from .prompts import CODER_SYSTEM

_REFLECTION_TEMPLATE = """⚠️ 上次代码执行报错，请分析原因并修复：

错误信息：
{error}

请重新生成正确的代码，调用 execute_python 执行。"""


class CoderAgent(BaseAgent):
    name = "coder"
    system_prompt = CODER_SYSTEM
    max_memory: int = 30   # Coder 保留更多历史以追踪代码上下文

    def _on_tool_error(self, tool_name: str, error_text: str) -> str | None:
        """工具执行失败时返回 reflection prompt 注入到对话，返回 None 则不注入。"""
        if tool_name == "execute_python":
            return _REFLECTION_TEMPLATE.format(error=error_text[:2000])
        return None

    async def run(self, user_input: str, images=None) -> str:  # type: ignore[override]
        from ..llm import ChatMessage as CM
        msg = CM.user(user_input, images)
        self.history.append(msg)
        await emit(EventType.AGENT_MESSAGE, self.task_id, agent=self.name,
                   role="user", content=user_input)

        consecutive_errors = 0
        max_consecutive_errors = 4  # 连续代码报错超过此次数则终止

        for step in range(self.max_iterations):
            await self.wait_if_paused()
            await emit(EventType.AGENT_THINKING, self.task_id, agent=self.name, step=step + 1)

            self._trim_history()
            tool_specs = self.tools.specs() if self.tools else None
            try:
                resp = await self._chat_with_retry(tool_specs)
            except Exception as e:
                logger.exception("Coder LLM error after retries")
                await emit(EventType.AGENT_MESSAGE, self.task_id, agent=self.name,
                           role="assistant", content=f"[LLM error] {e}")
                raise

            self.history.append(resp)
            await emit(EventType.AGENT_MESSAGE, self.task_id, agent=self.name,
                       role="assistant", content=resp.content, has_tools=bool(resp.tool_calls))

            if not resp.tool_calls:
                return resp.content

            for call in resp.tool_calls:
                fn = call.get("function", {})
                name = fn.get("name", "")
                raw_args = fn.get("arguments", "{}")
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except json.JSONDecodeError:
                    args = {}
                call_id = call.get("id", "")

                await emit(EventType.AGENT_TOOL_CALL, self.task_id, agent=self.name,
                           tool=name, args=args, call_id=call_id)

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
                    result_text = result_text[:6000] + f' ...(truncated {len(result_text)})'

                await emit(EventType.AGENT_TOOL_RESULT, self.task_id, agent=self.name,
                           tool=name, result=result, call_id=call_id)

                self.history.append(ChatMessage(
                    role="tool", content=result_text,
                    tool_call_id=call_id, name=name,
                ))

                # 代码执行失败：注入 reflection prompt 引导 LLM 修复
                # success=False / error 非空 / stderr 含 Traceback 均视为失败
                err_msg = result.get("error") or ""
                stderr_txt = result.get("stderr", "")
                has_traceback = "Traceback" in stderr_txt or "Error" in stderr_txt
                is_error = not result.get("success", True) or bool(err_msg) or has_traceback
                if is_error:
                    consecutive_errors += 1
                    combined_err = (err_msg + "\n" + stderr_txt).strip() or result_text
                    reflection = self._on_tool_error(name, combined_err)
                    if reflection and consecutive_errors <= max_consecutive_errors:
                        self.history.append(ChatMessage(role="user", content=reflection))
                        await emit(EventType.AGENT_MESSAGE, self.task_id, agent=self.name,
                                   role="user", content=f"[reflection] {reflection[:100]}...")
                    elif consecutive_errors > max_consecutive_errors:
                        logger.warning("Coder consecutive errors={}, giving up | task={}",
                                       consecutive_errors, self.task_id)
                        return f"代码执行反复失败（{consecutive_errors}次），最后错误：{result.get('error', '')}"
                else:
                    consecutive_errors = 0  # 成功则重置计数

        logger.warning("CoderAgent reached max_iterations | task={}", self.task_id)
        return self.history[-1].content if self.history else ""
