"""事件总线 - 支撑可视化追踪与 WebSocket 推送。

设计要点：
- 一切 Agent / Tool / Sandbox 行为都发布为事件
- 事件按 task_id 分组，可被 WebSocket 订阅
- 同时持久化到任务历史，供前端时间线回溯
"""
from __future__ import annotations

import asyncio
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EventType(str, Enum):
    # 任务级
    TASK_CREATED = "task.created"
    TASK_STARTED = "task.started"
    TASK_PAUSED = "task.paused"
    TASK_RESUMED = "task.resumed"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"

    # 工作流级
    PHASE_ENTER = "phase.enter"
    PHASE_EXIT = "phase.exit"

    # Agent 级
    AGENT_THINKING = "agent.thinking"        # 思考中（流式 token / 中间态）
    AGENT_MESSAGE = "agent.message"          # 完整消息
    AGENT_TOOL_CALL = "agent.tool_call"      # 工具调用请求
    AGENT_TOOL_RESULT = "agent.tool_result"  # 工具调用结果
    AGENT_LLM_USAGE = "agent.llm_usage"     # LLM 调用完成后 token 计量

    # 沙箱级
    SANDBOX_STDOUT = "sandbox.stdout"
    SANDBOX_STDERR = "sandbox.stderr"
    SANDBOX_RESULT = "sandbox.result"
    SANDBOX_DISPLAY = "sandbox.display"      # 图表 / 富媒体

    # HITL
    HITL_REQUEST = "hitl.request"            # 请求人工介入
    HITL_RESOLVED = "hitl.resolved"          # 已解决


@dataclass
class Event:
    type: EventType
    task_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    agent: str = ""
    timestamp: float = field(default_factory=time.time)
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "task_id": self.task_id,
            "agent": self.agent,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "event_id": self.event_id,
        }


class EventBus:
    """进程内异步事件总线 + 历史回放。"""

    def __init__(self) -> None:
        # task_id -> [queue]
        self._subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)
        # task_id -> [event]
        self._history: dict[str, list[Event]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def publish(self, event: Event) -> None:
        async with self._lock:
            self._history[event.task_id].append(event)
            queues = list(self._subscribers.get(event.task_id, []))
        for q in queues:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    async def subscribe(self, task_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1024)
        async with self._lock:
            self._subscribers[task_id].append(q)
            # 回放历史
            for ev in self._history.get(task_id, []):
                q.put_nowait(ev)
        return q

    async def unsubscribe(self, task_id: str, q: asyncio.Queue) -> None:
        async with self._lock:
            if task_id in self._subscribers and q in self._subscribers[task_id]:
                self._subscribers[task_id].remove(q)

    def history(self, task_id: str) -> list[Event]:
        return list(self._history.get(task_id, []))


# 全局单例
bus = EventBus()


# ---------- 便捷发布器 ----------
async def emit(
    type_: EventType,
    task_id: str,
    *,
    agent: str = "",
    **payload: Any,
) -> None:
    await bus.publish(Event(type=type_, task_id=task_id, agent=agent, payload=payload))
