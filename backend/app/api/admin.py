"""后台管理路由（仅 admin）：用户、任务、默认模型、用量统计。"""
from __future__ import annotations

import time
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import require_admin
from ..auth.security import hash_password
from ..db import ModelConfigRow, TaskRecord, User, UserRole, get_session
from ..services.model_service import DEFAULT_AGENTS, list_configs, upsert_config
from ..services.usage_service import stats_by_model, stats_by_user, stats_for_user, stats_overview
from ..tasks import task_manager

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


# ---------- Users ----------
class AdminUserOut(BaseModel):
    id: int
    username: str
    email: str
    role: str
    is_active: bool
    use_default_model: bool
    created_at: float
    last_login: float | None
    task_count: int = 0


class AdminUserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=32, pattern=r"^[a-zA-Z0-9_\-]+$")
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=128)
    role: str = Field(default=UserRole.USER.value, pattern=r"^(user|admin)$")


class AdminUserUpdate(BaseModel):
    email: EmailStr | None = None
    password: str | None = Field(default=None, min_length=6, max_length=128)
    role: str | None = Field(default=None, pattern=r"^(user|admin)$")
    is_active: bool | None = None
    use_default_model: bool | None = None


@router.get("/users", response_model=list[AdminUserOut])
async def list_users(session: AsyncSession = Depends(get_session)):
    users = (await session.execute(select(User).order_by(User.id))).scalars().all()
    counts = dict((await session.execute(
        select(TaskRecord.user_id, func.count(TaskRecord.task_id)).group_by(TaskRecord.user_id)
    )).all())
    return [
        AdminUserOut(
            id=u.id, username=u.username, email=u.email, role=u.role,
            is_active=u.is_active, use_default_model=u.use_default_model,
            created_at=u.created_at, last_login=u.last_login,
            task_count=counts.get(u.id, 0),
        )
        for u in users
    ]


@router.post("/users", response_model=AdminUserOut)
async def create_user(body: AdminUserCreate, session: AsyncSession = Depends(get_session)):
    exists = (await session.execute(
        select(User).where((User.username == body.username) | (User.email == body.email))
    )).scalar_one_or_none()
    if exists:
        raise HTTPException(409, "用户名或邮箱已被使用")
    u = User(
        username=body.username, email=body.email,
        hashed_password=hash_password(body.password),
        role=body.role, is_active=True, use_default_model=True,
    )
    session.add(u); await session.commit(); await session.refresh(u)
    return AdminUserOut(
        id=u.id, username=u.username, email=u.email, role=u.role,
        is_active=u.is_active, use_default_model=u.use_default_model,
        created_at=u.created_at, last_login=u.last_login, task_count=0,
    )


@router.patch("/users/{user_id}", response_model=AdminUserOut)
async def update_user(user_id: int, body: AdminUserUpdate, session: AsyncSession = Depends(get_session)):
    u = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not u:
        raise HTTPException(404, "用户不存在")
    if body.email: u.email = body.email
    if body.password: u.hashed_password = hash_password(body.password)
    if body.role: u.role = body.role
    if body.is_active is not None: u.is_active = body.is_active
    if body.use_default_model is not None: u.use_default_model = body.use_default_model
    await session.commit(); await session.refresh(u)
    cnt = (await session.execute(
        select(func.count(TaskRecord.task_id)).where(TaskRecord.user_id == u.id)
    )).scalar_one()
    return AdminUserOut(
        id=u.id, username=u.username, email=u.email, role=u.role,
        is_active=u.is_active, use_default_model=u.use_default_model,
        created_at=u.created_at, last_login=u.last_login, task_count=cnt,
    )


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: int,
    session: AsyncSession = Depends(get_session),
    me: User = Depends(require_admin),
):
    if user_id == me.id:
        raise HTTPException(400, "不能删除自己")
    u = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not u:
        raise HTTPException(404, "用户不存在")
    for t in [tt for tt in task_manager.list_all() if tt.user_id == user_id]:
        await task_manager.delete(t.task_id)
    await session.delete(u); await session.commit()
    return {"ok": True}


# ---------- Tasks ----------
class AdminTaskOut(BaseModel):
    task_id: str; user_id: int; username: str
    title: str; state: str; phase: str
    created_at: float; updated_at: float


@router.get("/tasks", response_model=list[AdminTaskOut])
async def list_all_tasks(session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(
        select(TaskRecord, User.username)
        .join(User, User.id == TaskRecord.user_id)
        .order_by(TaskRecord.created_at.desc())
    )).all()
    return [
        AdminTaskOut(
            task_id=r.task_id, user_id=r.user_id, username=username,
            title=r.title, state=r.state, phase=r.phase,
            created_at=r.created_at, updated_at=r.updated_at,
        )
        for r, username in rows
    ]


@router.delete("/tasks/{task_id}")
async def admin_delete_task(task_id: str):
    await task_manager.delete(task_id)
    return {"ok": True}


# ---------- Default models ----------
class DefaultModelUpdate(BaseModel):
    agent: str = Field(..., pattern=r"^(default|modeler|coder|writer)$")
    backend: Optional[str] = Field(None, pattern=r"^(openai|litellm)$")
    model: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    temperature: Optional[float] = Field(None, ge=0, le=2)
    price_prompt_per_1k: Optional[float] = Field(None, ge=0)
    price_completion_per_1k: Optional[float] = Field(None, ge=0)
    max_tokens: Optional[int] = Field(None, ge=0)


@router.get("/models")
async def get_defaults(session: AsyncSession = Depends(get_session)):
    cfg = await list_configs(session, owner_id=None)
    return {"agents": DEFAULT_AGENTS, "defaults": cfg}


@router.post("/models")
async def update_default(body: DefaultModelUpdate, session: AsyncSession = Depends(get_session)):
    await upsert_config(
        session, owner_id=None,
        agent=body.agent, backend=body.backend, model=body.model,
        base_url=body.base_url, api_key=body.api_key, temperature=body.temperature,
        price_prompt_per_1k=body.price_prompt_per_1k,
        price_completion_per_1k=body.price_completion_per_1k,
        max_tokens=body.max_tokens,
    )
    await session.commit()
    return {"ok": True}


# ---------- Stats ----------
class StatsOut(BaseModel):
    users: int
    active_users: int
    tasks: int
    tasks_by_state: dict[str, int]
    running_in_memory: int
    uptime_hint: str
    usage: dict[str, Any]


@router.get("/stats", response_model=StatsOut)
async def stats(session: AsyncSession = Depends(get_session)):
    total_users = (await session.execute(select(func.count(User.id)))).scalar_one()
    active = (await session.execute(
        select(func.count(User.id)).where(User.is_active == True)  # noqa: E712
    )).scalar_one()
    total_tasks = (await session.execute(select(func.count(TaskRecord.task_id)))).scalar_one()
    by_state_rows = (await session.execute(
        select(TaskRecord.state, func.count(TaskRecord.task_id)).group_by(TaskRecord.state)
    )).all()
    by_state = {s: c for s, c in by_state_rows}
    in_mem = sum(
        1 for t in task_manager.list_all()
        if t.state.value in ("running", "paused", "awaiting_hitl")
    )
    usage = await stats_overview(session)
    return StatsOut(
        users=total_users, active_users=active,
        tasks=total_tasks, tasks_by_state=by_state,
        running_in_memory=in_mem,
        uptime_hint=time.strftime("%Y-%m-%d %H:%M:%S"),
        usage=usage,
    )


# ---------- Usage ----------
@router.get("/usage/overview")
async def usage_overview(session: AsyncSession = Depends(get_session)):
    return await stats_overview(session)


@router.get("/usage/by-user")
async def usage_by_user(session: AsyncSession = Depends(get_session)):
    return await stats_by_user(session)


@router.get("/usage/by-model")
async def usage_by_model(session: AsyncSession = Depends(get_session)):
    return await stats_by_model(session)


@router.get("/users/{user_id}/usage")
async def usage_for_user(user_id: int, session: AsyncSession = Depends(get_session)):
    u = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not u:
        raise HTTPException(404, "用户不存在")
    data = await stats_for_user(session, user_id)
    return {"user": {"id": u.id, "username": u.username, "email": u.email, "role": u.role}, **data}
