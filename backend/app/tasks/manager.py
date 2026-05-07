"""任务管理器：状态机 + 多用户隔离 + HITL + 检查点。

存储两层：
- 进程内内存表：实时状态、暂停/HITL 信号、运行 handle
- 数据库（SQLAlchemy）：用户隔离、跨重启持久化、admin 查询

工作区路径：workspace/{user_id}/{task_id}/
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import select

from ..core.config import get_settings
from ..core.events import EventType, emit
from ..core.logging import logger


class TaskState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    AWAITING_HITL = "awaiting_hitl"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Task:
    task_id: str
    user_id: int
    title: str
    problem: str
    data_files: list[str] = field(default_factory=list)
    image_files: list[str] = field(default_factory=list)   # 随题提交的图片文件名
    state: TaskState = TaskState.PENDING
    phase: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    error: str = ""
    work_dir: str = ""
    checkpoints: list[dict[str, Any]] = field(default_factory=list)
    hitl_request: Optional[dict[str, Any]] = None
    hitl_response: Optional[dict[str, Any]] = None

    def touch(self) -> None:
        self.updated_at = time.time()


_TERMINAL_STATES = frozenset({
    TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED
})
_MAX_TASKS_IN_MEMORY = 500      # 超限时淘汰最老的已完结任务


class TaskManager:
    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._pause_events: dict[str, asyncio.Event] = {}
        self._hitl_events: dict[str, asyncio.Event] = {}
        self._task_handles: dict[str, asyncio.Task] = {}
        self._sandboxes: dict[str, Any] = {}  # task_id -> JupyterSandbox

    async def init(self) -> None:
        """从数据库恢复未结束的任务元信息（运行状态置为 cancelled，避免重启后悬挂）。"""
        try:
            from ..db import AsyncSessionLocal, TaskRecord
            async with AsyncSessionLocal() as s:
                rows = (await s.execute(select(TaskRecord))).scalars().all()
                for r in rows:
                    state = TaskState(r.state) if r.state in TaskState._value2member_map_ else TaskState.PENDING
                    if state in (TaskState.RUNNING, TaskState.PAUSED, TaskState.AWAITING_HITL):
                        # 重启前在运行中：标记为 cancelled
                        state = TaskState.CANCELLED
                        r.state = state.value
                    t = Task(
                        task_id=r.task_id, user_id=r.user_id,
                        title=r.title, problem=r.problem,
                        data_files=json.loads(r.data_files or "[]"),
                        image_files=json.loads(getattr(r, 'image_files', None) or "[]"),
                        state=state, phase=r.phase, error=r.error,
                        work_dir=r.work_dir,
                        created_at=r.created_at, updated_at=r.updated_at,
                    )
                    self._tasks[t.task_id] = t
                    ev = asyncio.Event(); ev.set()
                    self._pause_events[t.task_id] = ev
                    self._hitl_events[t.task_id] = asyncio.Event()
                await s.commit()
            logger.info("已恢复 {} 个历史任务", len(self._tasks))
        except Exception as e:
            logger.warning("任务恢复跳过: {}", e)

    async def close(self) -> None:
        pass

    # ---------- 创建 ----------
    async def create(
        self, *, user_id: int, title: str, problem: str,
        data_files: list[str], image_files: list[str] | None = None,
    ) -> Task:
        settings = get_settings()
        tid = uuid.uuid4().hex[:12]
        work_dir = settings.workspace_path / str(user_id) / tid
        work_dir.mkdir(parents=True, exist_ok=True)

        t = Task(
            task_id=tid, user_id=user_id,
            title=title, problem=problem,
            data_files=data_files,
            image_files=image_files or [],
            work_dir=str(work_dir),
        )
        self._tasks[tid] = t
        ev = asyncio.Event(); ev.set()
        self._pause_events[tid] = ev
        self._hitl_events[tid] = asyncio.Event()

        # 入库
        from ..db import AsyncSessionLocal, TaskRecord
        async with AsyncSessionLocal() as s:
            rec = TaskRecord(
                task_id=tid, user_id=user_id, title=title, problem=problem,
                state=t.state.value, phase=t.phase, error="",
                work_dir=t.work_dir,
                data_files=json.dumps(data_files),
                image_files=json.dumps(image_files or []),
                created_at=t.created_at, updated_at=t.updated_at,
            )
            s.add(rec)
            await s.commit()

        await emit(EventType.TASK_CREATED, tid, title=title)
        self._maybe_evict()
        return t

    async def update_data_files(self, task_id: str, files: list[str]) -> None:
        t = self._tasks.get(task_id)
        if not t:
            return
        t.data_files = files
        await self._persist_task(t)

    async def update_image_files(self, task_id: str, files: list[str]) -> None:
        t = self._tasks.get(task_id)
        if not t:
            return
        t.image_files = files
        await self._persist_task(t)

    # ---------- 状态机 ----------
    async def update_state(self, task_id: str, state: TaskState, *, error: str = "", phase: str = "") -> None:
        t = self._tasks.get(task_id)
        if not t:
            return
        t.state = state
        if error:
            t.error = error
        if phase:
            t.phase = phase
        # 终态自动清空 phase，避免前端状态栏残留
        if state in _TERMINAL_STATES:
            t.phase = ""
        t.touch()
        await self._persist_task(t)

    def get(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def get_for_user(self, task_id: str, user_id: int, is_admin: bool = False) -> Optional[Task]:
        t = self._tasks.get(task_id)
        if not t:
            return None
        if is_admin or t.user_id == user_id:
            return t
        return None

    def list_for_user(self, user_id: int) -> list[Task]:
        return sorted(
            [t for t in self._tasks.values() if t.user_id == user_id],
            key=lambda x: -x.created_at,
        )

    def list_all(self) -> list[Task]:
        return sorted(self._tasks.values(), key=lambda x: -x.created_at)

    # ---------- HITL / 暂停 ----------
    async def pause(self, task_id: str) -> None:
        if task_id in self._pause_events:
            self._pause_events[task_id].clear()
            await self.update_state(task_id, TaskState.PAUSED)
            await emit(EventType.TASK_PAUSED, task_id)

    async def resume(self, task_id: str) -> None:
        if task_id in self._pause_events:
            self._pause_events[task_id].set()
            await self.update_state(task_id, TaskState.RUNNING)
            await emit(EventType.TASK_RESUMED, task_id)

    async def cancel(self, task_id: str) -> None:
        # 若任务处于暂停状态，先解除 wait() 阻塞，让 CancelledError 能正常传播
        pause_ev = self._pause_events.get(task_id)
        if pause_ev:
            pause_ev.set()
        # 同理解除 HITL 阻塞
        hitl_ev = self._hitl_events.get(task_id)
        if hitl_ev:
            hitl_ev.set()
        # 取消 asyncio task（发送 CancelledError）
        handle = self._task_handles.get(task_id)
        if handle and not handle.done():
            handle.cancel()
        await self.update_state(task_id, TaskState.CANCELLED)
        await emit(EventType.TASK_CANCELLED, task_id)

    async def wait_if_paused(self, task_id: str) -> None:
        ev = self._pause_events.get(task_id)
        if ev:
            await ev.wait()

    async def request_hitl(self, task_id: str, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        t = self._tasks.get(task_id)
        if not t:
            return {}
        t.hitl_request = {"prompt": prompt, "context": context, "ts": time.time()}
        t.hitl_response = None
        await self.update_state(task_id, TaskState.AWAITING_HITL)
        await emit(EventType.HITL_REQUEST, task_id, prompt=prompt, context=context)
        ev = self._hitl_events[task_id]
        ev.clear()
        await ev.wait()
        resp = t.hitl_response or {}
        await emit(EventType.HITL_RESOLVED, task_id, response=resp)
        await self.update_state(task_id, TaskState.RUNNING)
        return resp

    async def reply_hitl(self, task_id: str, response: dict[str, Any]) -> None:
        t = self._tasks.get(task_id)
        if not t:
            return
        t.hitl_response = response
        t.hitl_request = None
        ev = self._hitl_events.get(task_id)
        if ev:
            ev.set()

    # ---------- 检查点 ----------
    async def checkpoint(self, task_id: str, label: str, data: dict[str, Any]) -> None:
        t = self._tasks.get(task_id)
        if not t:
            return
        t.checkpoints.append({"label": label, "ts": time.time(), "data": data})

    def get_checkpoint(self, task_id: str, label: str) -> Optional[dict[str, Any]]:
        t = self._tasks.get(task_id)
        if not t:
            return None
        for cp in reversed(t.checkpoints):
            if cp["label"] == label:
                return cp["data"]
        return None

    async def rollback_to(self, task_id: str, label: str) -> bool:
        t = self._tasks.get(task_id)
        if not t:
            return False
        idx = -1
        for i, cp in enumerate(t.checkpoints):
            if cp["label"] == label:
                idx = i; break
        if idx < 0:
            return False
        t.checkpoints = t.checkpoints[:idx]
        return True

    # ---------- 删除 ----------
    async def delete(self, task_id: str) -> None:
        t = self._tasks.pop(task_id, None)
        self._pause_events.pop(task_id, None)
        self._hitl_events.pop(task_id, None)
        h = self._task_handles.pop(task_id, None)
        if h and not h.done():
            h.cancel()
        # 数据库删除
        try:
            from ..db import AsyncSessionLocal, TaskRecord
            async with AsyncSessionLocal() as s:
                rec = (await s.execute(
                    select(TaskRecord).where(TaskRecord.task_id == task_id)
                )).scalar_one_or_none()
                if rec:
                    await s.delete(rec)
                    await s.commit()
        except Exception as e:
            logger.warning("DB delete task failed: {}", e)
        # 工作区清理
        if t and t.work_dir:
            try:
                import shutil
                shutil.rmtree(t.work_dir, ignore_errors=True)
            except Exception:
                pass

    def attach_handle(self, task_id: str, handle: asyncio.Task) -> None:
        self._task_handles[task_id] = handle

    # ---------- Sandbox 注册表 ----------
    def register_sandbox(self, task_id: str, sandbox: Any) -> None:
        """注册活跃的 JupyterSandbox（orchestrator 在 async with 内调用）。"""
        self._sandboxes[task_id] = sandbox

    def unregister_sandbox(self, task_id: str) -> None:
        self._sandboxes.pop(task_id, None)

    async def interrupt_task(self, task_id: str) -> bool:
        """向活跃 sandbox 发送中断信号。无活跃 sandbox 返回 False。"""
        sandbox = self._sandboxes.get(task_id)
        if sandbox:
            await sandbox.interrupt()
            return True
        return False

    def _maybe_evict(self) -> None:
        """内存任务数超限时，淘汰最老的已完结任务，防止内存无限增长。"""
        if len(self._tasks) <= _MAX_TASKS_IN_MEMORY:
            return
        evictable = sorted(
            [t for t in self._tasks.values() if t.state in _TERMINAL_STATES],
            key=lambda x: x.updated_at,
        )
        for t in evictable[: len(self._tasks) - _MAX_TASKS_IN_MEMORY]:
            self._tasks.pop(t.task_id, None)
            self._pause_events.pop(t.task_id, None)
            self._hitl_events.pop(t.task_id, None)
            logger.debug("Evicted task {} from memory", t.task_id)

    # ---------- 持久化 ----------
    async def _persist_task(self, t: Task) -> None:
        try:
            from ..db import AsyncSessionLocal, TaskRecord
            async with AsyncSessionLocal() as s:
                rec = (await s.execute(
                    select(TaskRecord).where(TaskRecord.task_id == t.task_id)
                )).scalar_one_or_none()
                if not rec:
                    return
                rec.state = t.state.value
                rec.phase = t.phase
                rec.error = t.error
                rec.data_files = json.dumps(t.data_files)
                rec.image_files = json.dumps(t.image_files)
                rec.updated_at = t.updated_at
                await s.commit()
        except Exception as e:
            logger.debug("persist skip: {}", e)


task_manager = TaskManager()
