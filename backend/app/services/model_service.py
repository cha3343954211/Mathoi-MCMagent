"""模型配置服务：全局默认 vs 用户自定义 vs 预设；带 Fernet 加密。"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.crypto import decrypt, encrypt
from ..db import ModelConfigRow, ModelPreset, User

DEFAULT_AGENTS = ["default", "coordinator", "modeler", "coder", "writer"]
PRESET_AGENTS = ["all", "default", "coordinator", "modeler", "coder", "writer"]


@dataclass
class ResolvedConfig:
    agent: str
    backend: str
    model: str
    base_url: str
    api_key: str
    temperature: float
    price_prompt_per_1k: float
    price_completion_per_1k: float
    is_default: bool  # True: 来源于管理员全局默认；False: 用户自定义
    max_tokens: Optional[int] = None


async def _fetch(session: AsyncSession, owner_id: Optional[int], agent: str) -> Optional[ModelConfigRow]:
    stmt = select(ModelConfigRow).where(
        ModelConfigRow.agent == agent,
        ModelConfigRow.owner_id.is_(None) if owner_id is None else ModelConfigRow.owner_id == owner_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def _fetch_with_fallback(
    session: AsyncSession, owner_id: Optional[int], agent: str
) -> Optional[ModelConfigRow]:
    row = await _fetch(session, owner_id, agent)
    if row and (row.model or row.api_key_enc):
        return row
    if agent != "default":
        row2 = await _fetch(session, owner_id, "default")
        if row2 and (row2.model or row2.api_key_enc):
            return row2
    return row  # 可能为 None 或空壳


def _row_to_resolved(row: ModelConfigRow, agent: str, is_default: bool) -> ResolvedConfig:
    return ResolvedConfig(
        agent=agent,
        backend=row.backend or "openai",
        model=row.model or "",
        base_url=row.base_url or "",
        api_key=decrypt(row.api_key_enc),
        temperature=row.temperature,
        price_prompt_per_1k=row.price_prompt_per_1k,
        price_completion_per_1k=row.price_completion_per_1k,
        is_default=is_default,
        max_tokens=row.max_tokens,
    )


async def _fetch_preset(session: AsyncSession, preset_id: int) -> Optional[ModelPreset]:
    return (await session.execute(
        select(ModelPreset).where(ModelPreset.id == preset_id, ModelPreset.is_active.is_(True))
    )).scalar_one_or_none()


def _agent_matches(preset_agent: str, target: str) -> bool:
    """检查预设的 agent 字段（逗号分隔或单值）是否包含目标 agent。"""
    agents = {a.strip() for a in preset_agent.split(',') if a.strip()}
    return 'all' in agents or target in agents


async def get_default_preset(session: AsyncSession, agent: str) -> Optional[ModelPreset]:
    """获取该 agent 的默认预设（is_default=True & is_active=True）。
    先找 agent-specific，再找 'all'。"""
    stmt = (
        select(ModelPreset)
        .where(ModelPreset.is_active.is_(True), ModelPreset.is_default.is_(True))
        .order_by(ModelPreset.sort_order, ModelPreset.id)
    )
    all_defaults = list((await session.execute(stmt)).scalars().all())
    matching = [p for p in all_defaults if _agent_matches(p.agent, agent)]
    # 优先返回 agent-specific（非纯 'all'）
    specific = [p for p in matching if p.agent != 'all']
    return specific[0] if specific else (matching[0] if matching else None)


async def set_default_preset(session: AsyncSession, preset_id: int) -> ModelPreset:
    """将指定预设设为默认，同时清除同 agent 其余预设的 is_default。"""
    p = (await session.execute(
        select(ModelPreset).where(ModelPreset.id == preset_id)
    )).scalar_one_or_none()
    if p is None:
        raise ValueError(f"preset {preset_id} not found")
    # 清除同 agent 其他预设的 is_default
    others = list((await session.execute(
        select(ModelPreset).where(
            ModelPreset.is_default.is_(True),
            ModelPreset.agent == p.agent,
            ModelPreset.id != preset_id,
        )
    )).scalars().all())
    for ep in others:
        ep.is_default = False
    p.is_default = True
    await session.flush()
    return p


def _preset_to_resolved(p: ModelPreset, agent: str) -> ResolvedConfig:
    return ResolvedConfig(
        agent=agent,
        backend=p.backend or "openai",
        model=p.model or "",
        base_url=p.base_url or "",
        api_key=decrypt(p.api_key_enc),
        temperature=p.temperature,
        price_prompt_per_1k=p.price_prompt_per_1k,
        price_completion_per_1k=p.price_completion_per_1k,
        is_default=False,
        max_tokens=p.max_tokens,
    )


async def resolve_effective(
    session: AsyncSession, *, user: User, agent: str
) -> ResolvedConfig:
    """返回该用户在某 agent 下真实使用的配置。

    决策优先级：
    1. 用户显式选择了预设（selected_preset_id）→ 使用该预设
    2. 用户自定义（model+key 有效）→ 用户自定义
    3. 管理员标记的默认预设（is_default=True）→ 自动兜底
    4. env 级全局默认（ModelConfigRow owner_id=None）
    """
    # 1. 用户显式预设
    user_row = await _fetch(session, user.id, agent)
    if user_row and user_row.selected_preset_id:
        p = await _fetch_preset(session, user_row.selected_preset_id)
        if p:
            return _preset_to_resolved(p, agent)

    # 2. 用户自定义
    row = await _fetch_with_fallback(session, user.id, agent)
    if row and row.model and row.api_key_enc:
        return _row_to_resolved(row, agent, is_default=False)

    # 3. 管理员默认预设
    default_p = await get_default_preset(session, agent)
    if default_p:
        return _preset_to_resolved(default_p, agent)

    # 4. env 级兜底
    row = await _fetch_with_fallback(session, None, agent)
    if row:
        return _row_to_resolved(row, agent, is_default=True)
    return ResolvedConfig(
        agent=agent, backend="openai", model="", base_url="", api_key="",
        temperature=0.2, price_prompt_per_1k=0.0, price_completion_per_1k=0.0,
        is_default=True,
    )


# ── 预设 CRUD ─────────────────────────────────────────────────────────────────

async def list_presets(session: AsyncSession, agent: Optional[str] = None,
                       active_only: bool = True) -> list[ModelPreset]:
    """列出预设。agent=None/'all' 返回全部；agent=specific 用 Python 层过滤以支持逗号多值。"""
    stmt = select(ModelPreset)
    if active_only:
        stmt = stmt.where(ModelPreset.is_active.is_(True))
    stmt = stmt.order_by(ModelPreset.sort_order, ModelPreset.id)
    all_presets = list((await session.execute(stmt)).scalars().all())
    if agent and agent != "all":
        all_presets = [p for p in all_presets if _agent_matches(p.agent, agent)]
    return all_presets


async def create_preset(
    session: AsyncSession, *, name: str, agent: str = "all",
    backend: str = "openai", model: str, base_url: str = "",
    api_key: str = "", temperature: float = 0.2,
    max_tokens: Optional[int] = None, description: str = "",
    price_prompt_per_1k: float = 0.0, price_completion_per_1k: float = 0.0,
    sort_order: int = 0, is_default: bool = False, pro_only: bool = False,
) -> ModelPreset:
    p = ModelPreset(
        name=name, description=description, agent=agent, backend=backend,
        model=model, base_url=base_url,
        api_key_enc=encrypt(api_key) if api_key else "",
        temperature=temperature, max_tokens=max_tokens,
        price_prompt_per_1k=price_prompt_per_1k,
        price_completion_per_1k=price_completion_per_1k,
        sort_order=sort_order, is_active=True, is_default=is_default,
        pro_only=pro_only,
    )
    session.add(p)
    await session.flush()
    return p


async def update_preset(
    session: AsyncSession, preset_id: int, **kwargs
) -> ModelPreset:
    p = (await session.execute(
        select(ModelPreset).where(ModelPreset.id == preset_id)
    )).scalar_one_or_none()
    if p is None:
        raise ValueError(f"preset {preset_id} not found")
    for k, v in kwargs.items():
        if k == "api_key":
            if v is not None and v != "":
                p.api_key_enc = encrypt(v) if v != "__clear__" else ""
        elif hasattr(p, k):
            setattr(p, k, v)
    p.updated_at = time.time()
    await session.flush()
    return p


async def delete_preset(session: AsyncSession, preset_id: int) -> None:
    p = (await session.execute(
        select(ModelPreset).where(ModelPreset.id == preset_id)
    )).scalar_one_or_none()
    if p:
        await session.delete(p)
        await session.flush()


async def select_user_preset(
    session: AsyncSession, *, user_id: int, agent: str,
    preset_id: Optional[int],
) -> None:
    """用户为某个 agent 选择（或清除）预设。"""
    stmt = select(ModelConfigRow).where(
        ModelConfigRow.owner_id == user_id,
        ModelConfigRow.agent == agent,
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        row = ModelConfigRow(owner_id=user_id, agent=agent)
        session.add(row)
    row.selected_preset_id = preset_id
    row.updated_at = time.time()
    await session.flush()


async def list_configs(session: AsyncSession, owner_id: Optional[int]) -> dict[str, dict]:
    """返回 agent -> {backend, model, base_url, has_api_key, temperature, price_*}."""
    stmt = select(ModelConfigRow).where(
        ModelConfigRow.owner_id.is_(None) if owner_id is None else ModelConfigRow.owner_id == owner_id
    )
    rows = (await session.execute(stmt)).scalars().all()
    out: dict[str, dict] = {}
    for r in rows:
        out[r.agent] = {
            "agent": r.agent,
            "backend": r.backend,
            "model": r.model,
            "base_url": r.base_url,
            "has_api_key": bool(r.api_key_enc),
            "temperature": r.temperature,
            "price_prompt_per_1k": r.price_prompt_per_1k,
            "price_completion_per_1k": r.price_completion_per_1k,
            "max_tokens": r.max_tokens,
            "selected_preset_id": r.selected_preset_id,
            "updated_at": r.updated_at,
        }
    # 补齐未配置的 agent
    for a in DEFAULT_AGENTS:
        out.setdefault(a, {
            "agent": a, "backend": "openai", "model": "", "base_url": "",
            "has_api_key": False, "temperature": 0.2,
            "price_prompt_per_1k": 0.0, "price_completion_per_1k": 0.0,
            "max_tokens": None, "selected_preset_id": None, "updated_at": 0.0,
        })
    return out


async def upsert_config(
    session: AsyncSession,
    *,
    owner_id: Optional[int],
    agent: str,
    backend: Optional[str] = None,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    temperature: Optional[float] = None,
    price_prompt_per_1k: Optional[float] = None,
    price_completion_per_1k: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> ModelConfigRow:
    row = await _fetch(session, owner_id, agent)
    if row is None:
        row = ModelConfigRow(owner_id=owner_id, agent=agent)
        session.add(row)
    if backend is not None:
        row.backend = backend
    if model is not None:
        row.model = model
    if base_url is not None:
        row.base_url = base_url
    if api_key is not None and api_key != "":
        # 空字符串视为"不改动"；如需清空请传特殊标记"__clear__"
        row.api_key_enc = encrypt(api_key) if api_key != "__clear__" else ""
    if temperature is not None:
        row.temperature = temperature
    if price_prompt_per_1k is not None:
        row.price_prompt_per_1k = price_prompt_per_1k
    if price_completion_per_1k is not None:
        row.price_completion_per_1k = price_completion_per_1k
    if max_tokens is not None:
        row.max_tokens = max_tokens if max_tokens > 0 else None  # 0 视为清空
    row.updated_at = time.time()
    await session.flush()
    return row


async def seed_defaults_if_empty(session: AsyncSession, env_defaults: dict[str, dict]) -> None:
    """首次启动：如果 model_configs 无全局默认，用 env 初始化。

    env_defaults: {"default": {"backend":..., "model":..., "base_url":..., "api_key":..., "temperature":...}, ...}
    """
    existing = (await session.execute(
        select(ModelConfigRow).where(ModelConfigRow.owner_id.is_(None))
    )).scalars().all()
    if existing:
        return
    for agent, data in env_defaults.items():
        row = ModelConfigRow(
            owner_id=None,
            agent=agent,
            backend=data.get("backend", "openai"),
            model=data.get("model", ""),
            base_url=data.get("base_url", ""),
            api_key_enc=encrypt(data.get("api_key", "")) if data.get("api_key") else "",
            temperature=data.get("temperature", 0.2),
        )
        session.add(row)
    await session.flush()
