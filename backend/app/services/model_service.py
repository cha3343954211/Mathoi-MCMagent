"""模型配置服务：全局默认 vs 用户自定义；带 Fernet 加密。"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.crypto import decrypt, encrypt
from ..db import ModelConfigRow, User

DEFAULT_AGENTS = ["default", "modeler", "coder", "writer"]


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


async def resolve_effective(
    session: AsyncSession, *, user: User, agent: str
) -> ResolvedConfig:
    """返回该用户在某 agent 下真实使用的配置。

    决策：
    1. user.use_default_model=True → 全局默认；否则用户自定义
    2. 指定 agent 若无有效配置（model+key 为空），回退到同层级的 default
    3. 用户自定义若为空，再回退到全局默认
    """
    use_default = user.use_default_model
    if not use_default:
        row = await _fetch_with_fallback(session, user.id, agent)
        if row and row.model and row.api_key_enc:
            return _row_to_resolved(row, agent, is_default=False)
        # 用户自定义缺失 → 回退到默认
    row = await _fetch_with_fallback(session, None, agent)
    if row:
        return _row_to_resolved(row, agent, is_default=True)
    return ResolvedConfig(
        agent=agent, backend="openai", model="", base_url="", api_key="",
        temperature=0.2, price_prompt_per_1k=0.0, price_completion_per_1k=0.0,
        is_default=True,
    )


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
            "updated_at": r.updated_at,
        }
    # 补齐未配置的 agent
    for a in DEFAULT_AGENTS:
        out.setdefault(a, {
            "agent": a, "backend": "openai", "model": "", "base_url": "",
            "has_api_key": False, "temperature": 0.2,
            "price_prompt_per_1k": 0.0, "price_completion_per_1k": 0.0,
            "max_tokens": None, "updated_at": 0.0,
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
