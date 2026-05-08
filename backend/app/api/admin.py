"""后台管理路由（仅 admin）：用户、任务、默认模型、用量统计、文件管理。"""
from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import require_admin
from ..auth.security import hash_password
from ..db import ModelConfigRow, ModelPreset, SystemSetting, TaskRecord, User, UserRole, get_session
from ..services.model_service import (
    PRESET_AGENTS, create_preset, delete_preset, list_configs, list_presets,
    select_user_preset, set_default_preset, upsert_config, update_preset,
)
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
    role: str = Field(default=UserRole.USER.value, pattern=r"^(user|pro|admin)$")


class AdminUserUpdate(BaseModel):
    email: EmailStr | None = None
    password: str | None = Field(default=None, min_length=6, max_length=128)
    role: str | None = Field(default=None, pattern=r"^(user|pro|admin)$")
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
    agent: str = Field(..., pattern=r"^(default|coordinator|modeler|coder|writer)$")
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


# ---------- Model Presets ----------
def _preset_out(p) -> dict:
    return {
        "id": p.id, "name": p.name, "description": p.description,
        "agent": p.agent, "backend": p.backend, "model": p.model,
        "base_url": p.base_url, "has_api_key": bool(p.api_key_enc),
        "temperature": p.temperature, "max_tokens": p.max_tokens,
        "price_prompt_per_1k": p.price_prompt_per_1k,
        "price_completion_per_1k": p.price_completion_per_1k,
        "is_active": p.is_active, "is_default": p.is_default,
        "pro_only": p.pro_only,
        "sort_order": p.sort_order, "created_at": p.created_at,
    }


class PresetCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: str = ""
    agent: str = Field(default="all")
    backend: str = Field(default="openai", pattern=r"^(openai|litellm)$")
    model: str = Field(..., min_length=1)
    base_url: str = ""
    api_key: str = ""
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_tokens: Optional[int] = Field(default=None, ge=0)
    price_prompt_per_1k: float = Field(default=0.0, ge=0)
    price_completion_per_1k: float = Field(default=0.0, ge=0)
    sort_order: int = 0
    is_default: bool = False
    pro_only: bool = False


class PresetUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    description: Optional[str] = None
    agent: Optional[str] = None
    backend: Optional[str] = Field(default=None, pattern=r"^(openai|litellm)$")
    model: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None   # "" = 不改；"__clear__" = 清空
    temperature: Optional[float] = Field(default=None, ge=0, le=2)
    max_tokens: Optional[int] = Field(default=None, ge=0)
    price_prompt_per_1k: Optional[float] = Field(default=None, ge=0)
    price_completion_per_1k: Optional[float] = Field(default=None, ge=0)
    is_active: Optional[bool] = None
    pro_only: Optional[bool] = None
    sort_order: Optional[int] = None


@router.get("/presets")
async def list_all_presets(
    agent: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
):
    presets = await list_presets(session, agent=agent, active_only=False)
    return {"presets": [_preset_out(p) for p in presets], "agents": PRESET_AGENTS}


@router.post("/presets")
async def admin_create_preset(body: PresetCreate, session: AsyncSession = Depends(get_session)):
    p = await create_preset(
        session, name=body.name, description=body.description, agent=body.agent,
        backend=body.backend, model=body.model, base_url=body.base_url,
        api_key=body.api_key, temperature=body.temperature,
        max_tokens=body.max_tokens,
        price_prompt_per_1k=body.price_prompt_per_1k,
        price_completion_per_1k=body.price_completion_per_1k,
        sort_order=body.sort_order, is_default=body.is_default,
        pro_only=body.pro_only,
    )
    await session.commit()
    return _preset_out(p)


@router.put("/presets/{preset_id}")
async def admin_update_preset(
    preset_id: int, body: PresetUpdate,
    session: AsyncSession = Depends(get_session),
):
    kwargs = {k: v for k, v in body.model_dump().items() if v is not None or k in ("api_key",)}
    try:
        p = await update_preset(session, preset_id, **kwargs)
        await session.commit()
        return _preset_out(p)
    except ValueError as e:
        raise HTTPException(404, str(e))


class PresetReorderItem(BaseModel):
    id: int
    sort_order: int


@router.put("/presets/reorder")
async def admin_reorder_presets(
    items: list[PresetReorderItem],
    session: AsyncSession = Depends(get_session),
):
    """批量更新预设排序（拖拽排序后调用）。"""
    for item in items:
        p = (await session.execute(select(ModelPreset).where(ModelPreset.id == item.id))).scalar_one_or_none()
        if p:
            p.sort_order = item.sort_order
    await session.commit()
    return {"ok": True}


@router.delete("/presets/{preset_id}")
async def admin_delete_preset(preset_id: int, session: AsyncSession = Depends(get_session)):
    await delete_preset(session, preset_id)
    await session.commit()
    return {"ok": True}


@router.post("/presets/{preset_id}/set-default")
async def admin_set_default_preset(
    preset_id: int, session: AsyncSession = Depends(get_session)
):
    """将指定预设设为该 agent 的默认预设（替代全局默认配置）。"""
    try:
        p = await set_default_preset(session, preset_id)
        await session.commit()
        return _preset_out(p)
    except ValueError as e:
        raise HTTPException(404, str(e))


# ---------- File Management ----------

class UserFileStat(BaseModel):
    user_id: int
    username: str
    task_count: int
    file_count: int
    total_size: int   # bytes


class TaskFileStat(BaseModel):
    task_id: str
    title: str
    state: str
    file_count: int
    total_size: int
    work_dir: str


def _scan_dir(path: str) -> tuple[int, int]:
    """返回 (文件数, 总字节数)。目录不存在则 (0, 0)。"""
    p = Path(path)
    if not p.exists():
        return 0, 0
    count = size = 0
    for f in p.rglob("*"):
        if f.is_file():
            count += 1
            size += f.stat().st_size
    return count, size


@router.get("/files/users", response_model=list[UserFileStat])
async def list_user_files(session: AsyncSession = Depends(get_session)):
    """列出所有用户的工作区文件统计。"""
    users = (await session.execute(select(User).order_by(User.id))).scalars().all()
    result = []
    for u in users:
        tasks = (await session.execute(
            select(TaskRecord).where(TaskRecord.user_id == u.id)
        )).scalars().all()
        total_files = total_size = 0
        for t in tasks:
            fc, sz = _scan_dir(t.work_dir)
            total_files += fc
            total_size += sz
        result.append(UserFileStat(
            user_id=u.id, username=u.username,
            task_count=len(tasks),
            file_count=total_files, total_size=total_size,
        ))
    return result


@router.get("/files/users/{user_id}", response_model=list[TaskFileStat])
async def list_user_task_files(
    user_id: int, session: AsyncSession = Depends(get_session)
):
    """列出指定用户下各任务的文件详情。"""
    u = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not u:
        raise HTTPException(404, "用户不存在")
    tasks = (await session.execute(
        select(TaskRecord).where(TaskRecord.user_id == user_id).order_by(TaskRecord.created_at.desc())
    )).scalars().all()
    result = []
    for t in tasks:
        fc, sz = _scan_dir(t.work_dir)
        result.append(TaskFileStat(
            task_id=t.task_id, title=t.title, state=t.state,
            file_count=fc, total_size=sz, work_dir=t.work_dir,
        ))
    return result


@router.delete("/files/tasks/{task_id}")
async def admin_clean_task_files(
    task_id: str, session: AsyncSession = Depends(get_session)
):
    """清理指定任务的工作区文件（保留任务记录，仅删磁盘文件）。"""
    t = (await session.execute(
        select(TaskRecord).where(TaskRecord.task_id == task_id)
    )).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "任务不存在")
    wd = Path(t.work_dir)
    if wd.exists():
        shutil.rmtree(str(wd), ignore_errors=True)
        wd.mkdir(parents=True, exist_ok=True)  # 保留空目录
    return {"ok": True, "cleaned": str(wd)}


@router.post("/files/gc")
async def gc_orphan_files(
    session: AsyncSession = Depends(get_session)
):
    """扫描 workspace 目录，清理没有对应任务记录的孤儿目录。"""
    from ..core.config import get_settings
    ws_root = get_settings().workspace_path
    if not ws_root.exists():
        return {"ok": True, "removed": [], "message": "workspace 目录不存在"}

    # 数据库中所有任务的 work_dir 集合
    all_tasks = (await session.execute(select(TaskRecord))).scalars().all()
    valid_dirs = {Path(t.work_dir).resolve() for t in all_tasks if t.work_dir}

    removed = []
    freed_bytes = 0
    for item in ws_root.iterdir():
        if not item.is_dir():
            continue
        if item.resolve() not in valid_dirs:
            _, sz = _scan_dir(str(item))
            freed_bytes += sz
            shutil.rmtree(str(item), ignore_errors=True)
            removed.append(item.name)

    return {"ok": True, "removed": removed, "freed_bytes": freed_bytes}


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


# ---------- System Settings ----------
# 白名单：仅允许通过 UI 修改这些键，避免任意键被注入
_ALLOWED_SETTING_KEYS = {"openalex_email", "daily_token_quota"}


class SettingsOut(BaseModel):
    openalex_email: str = ""
    openalex_email_source: str = "unset"   # 'db' | 'env' | 'unset'
    daily_token_quota: int = 0             # 0 = 不限制
    daily_token_quota_source: str = "env" # 'db' | 'env'


class SettingsUpdate(BaseModel):
    openalex_email: Optional[str] = Field(default=None, max_length=255)
    daily_token_quota: Optional[int] = Field(default=None, ge=0)


@router.get("/settings", response_model=SettingsOut)
async def get_system_settings(session: AsyncSession = Depends(get_session)):
    """获取系统设置。DB 值优先，env 作为兜底来源指示。"""
    from ..core.config import get_settings as _gs
    rows = (await session.execute(select(SystemSetting))).scalars().all()
    db_kv = {r.key: r.value for r in rows}

    env_email = (_gs().openalex_email or "").strip()
    db_email = (db_kv.get("openalex_email") or "").strip()

    db_quota_raw = db_kv.get("daily_token_quota", "")
    if db_quota_raw and db_quota_raw.isdigit():
        quota_val = int(db_quota_raw)
        quota_src = "db"
    else:
        quota_val = _gs().daily_token_quota
        quota_src = "env"

    email_val = db_email or env_email or ""
    email_src = "db" if db_email else ("env" if env_email else "unset")
    return SettingsOut(
        openalex_email=email_val, openalex_email_source=email_src,
        daily_token_quota=quota_val, daily_token_quota_source=quota_src,
    )


@router.put("/settings", response_model=SettingsOut)
async def update_system_settings(body: SettingsUpdate, session: AsyncSession = Depends(get_session)):
    """更新系统设置。空字符串视为清除 DB 值（回退 env）。"""
    updates = body.model_dump(exclude_none=True)
    for key, value in updates.items():
        if key not in _ALLOWED_SETTING_KEYS:
            continue
        # 简单校验
        if key == "openalex_email" and value:
            v = value.strip()
            if "@" not in v or "." not in v.split("@", 1)[-1]:
                raise HTTPException(400, "OpenAlex email 格式不正确")
            value = v
        if key == "daily_token_quota":
            value = str(max(0, int(value))) if value else "0"
        # upsert
        row = (await session.execute(
            select(SystemSetting).where(SystemSetting.key == key)
        )).scalar_one_or_none()
        if row is None:
            row = SystemSetting(key=key, value=value or "")
            session.add(row)
        else:
            row.value = value or ""
            row.updated_at = time.time()
    await session.commit()
    # 清除配额相关缓存（DB 值立即生效，无需等待 TTL）
    if "daily_token_quota" in updates:
        from ..services.usage_service import invalidate_quota_cache
        invalidate_quota_cache()
    return await get_system_settings(session)
