"""事件总线 - 支撑可视化追踪与 WebSocket 推送。

设计要点：
- 一切 Agent / Tool / Sandbox 行为都发布为事件
- 事件按 task_id 分组，可被 WebSocket 订阅
- 持久化到 DB（task_events 表），服务重启后可回放
- 随任务删除级联清理（CASCADE FK）
"""
from __future__ import annotations

import asyncio
import json
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
    TASK_CANCELLED = "task.cancelled"

    # 工作流级
    PHASE_ENTER = "phase.enter"
    PHASE_EXIT = "phase.exit"

    # Agent 级
    AGENT_THINKING = "agent.thinking"        # 思考中（中间态）
    AGENT_STREAM_CHUNK = "agent.stream_chunk" # 流式 token 块（不持久化历史）
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
    """进程内异步事件总线 + DB 持久化历史回放。"""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)
        # 内存缓存：运行期快速访问；重启后懒加载自 DB
        self._history: dict[str, list[Event]] = defaultdict(list)
        # 已从 DB 加载过的 task_id 集合（避免重复查询）
        self._db_loaded: set[str] = set()
        self._lock = asyncio.Lock()

    # 不写入 DB / 不进历史的事件类型（仅推送给当前活跃订阅者）
    _NO_PERSIST = frozenset({EventType.AGENT_STREAM_CHUNK})

    _MAX_HISTORY_PER_TASK = 2000   # 每任务最多保留事件数
    _TRIM_TO = 1500                # 超限后裁剪至此数量

    # ---- publish --------------------------------------------------------
    async def publish(self, event: Event) -> None:
        async with self._lock:
            if event.type not in self._NO_PERSIST:
                hist = self._history[event.task_id]
                hist.append(event)
                if len(hist) > self._MAX_HISTORY_PER_TASK:
                    self._history[event.task_id] = hist[-self._TRIM_TO:]
            queues = list(self._subscribers.get(event.task_id, []))
        for q in queues:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass
        # 持久化（fire-and-forget，不阻塞主流程）
        if event.type not in self._NO_PERSIST:
            asyncio.create_task(self._save_to_db(event))

    # ---- DB 写入 --------------------------------------------------------
    @staticmethod
    async def _save_to_db(event: Event) -> None:
        try:
            from ..db import AsyncSessionLocal, EventRecord
            from sqlalchemy import select as sa_select
            async with AsyncSessionLocal() as s:
                # 幂等：已存在则跳过
                exists = (await s.execute(
                    sa_select(EventRecord.id).where(EventRecord.event_id == event.event_id)
                )).scalar_one_or_none()
                if exists is None:
                    s.add(EventRecord(
                        event_id=event.event_id,
                        task_id=event.task_id,
                        type=event.type.value,
                        agent=event.agent,
                        payload=json.dumps(event.payload, default=str),
                        timestamp=event.timestamp,
                    ))
                    await s.commit()
        except Exception:
            pass  # 持久化失败不影响主流程

    # ---- DB 加载 --------------------------------------------------------
    async def _load_from_db(self, task_id: str) -> None:
        """从 DB 懒加载历史事件到内存缓存（每个 task_id 仅加载一次）。"""
        if task_id in self._db_loaded:
            return
        self._db_loaded.add(task_id)
        try:
            from ..db import AsyncSessionLocal, EventRecord
            from sqlalchemy import select as sa_select
            async with AsyncSessionLocal() as s:
                rows = (await s.execute(
                    sa_select(EventRecord)
                    .where(EventRecord.task_id == task_id)
                    .order_by(EventRecord.timestamp)
                )).scalars().all()
            events: list[Event] = []
            for r in rows:
                try:
                    etype = EventType(r.type)
                except ValueError:
                    continue
                events.append(Event(
                    type=etype, task_id=r.task_id, agent=r.agent,
                    payload=json.loads(r.payload or "{}"),
                    timestamp=r.timestamp, event_id=r.event_id,
                ))
            # 合并：以 DB 为准（去重后保持时序）
            existing_ids = {e.event_id for e in self._history.get(task_id, [])}
            new_events = [e for e in events if e.event_id not in existing_ids]
            merged = sorted(
                list(self._history.get(task_id, [])) + new_events,
                key=lambda e: e.timestamp,
            )
            self._history[task_id] = merged
        except Exception:
            pass

    # ---- subscribe ------------------------------------------------------
    async def subscribe(self, task_id: str) -> asyncio.Queue:
        # 先确保内存有历史（服务重启场景）
        await self._load_from_db(task_id)
        q: asyncio.Queue = asyncio.Queue(maxsize=2048)
        async with self._lock:
            self._subscribers[task_id].append(q)
            for ev in self._history.get(task_id, []):
                try:
                    q.put_nowait(ev)
                except asyncio.QueueFull:
                    break
        return q

    async def unsubscribe(self, task_id: str, q: asyncio.Queue) -> None:
        async with self._lock:
            subs = self._subscribers.get(task_id, [])
            if q in subs:
                subs.remove(q)

    # ---- history --------------------------------------------------------
    def history(self, task_id: str) -> list[Event]:
        """同步读内存缓存（仅用于任务运行期间）。"""
        return list(self._history.get(task_id, []))

    async def history_async(self, task_id: str) -> list[Event]:
        """异步读，自动从 DB 回填（用于 REST API）。"""
        await self._load_from_db(task_id)
        return list(self._history.get(task_id, []))

    # ---- 清理 -----------------------------------------------------------
    def evict(self, task_id: str) -> None:
        """任务删除时清除内存缓存（DB 由 CASCADE FK 自动清理）。"""
        self._history.pop(task_id, None)
        self._db_loaded.discard(task_id)
        self._subscribers.pop(task_id, None)


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
