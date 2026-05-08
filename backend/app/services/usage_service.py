"""LLM 用量 & 计费服务。"""
from __future__ import annotations

import time
from typing import Any, Optional

# 每日 token 查询缓存（避免每次 LLM 调用都查 DB）
# key: user_id  value: (total_tokens_today, cache_expire_ts)
_quota_cache: dict[int, tuple[int, float]] = {}
_QUOTA_CACHE_TTL = 60.0   # 缓存 60 秒

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import AsyncSessionLocal, UsageRecord, User


async def record_usage(
    *,
    user_id: int,
    task_id: Optional[str],
    agent: str,
    backend: str,
    model: str,
    is_default: bool,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cost_usd: float = 0.0,
    ok: bool = True,
    error: str = "",
) -> None:
    """独立 session，避免与调用方事务耦合。"""
    try:
        async with AsyncSessionLocal() as s:
            rec = UsageRecord(
                user_id=user_id, task_id=task_id, agent=agent, backend=backend,
                model=model, is_default=is_default,
                prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                cost_usd=cost_usd, ok=ok, error=error[:500],
                created_at=time.time(),
            )
            s.add(rec)
            await s.commit()
    except Exception:
        # 计量失败不应影响主流程
        pass


def _agg_cols():
    return (
        func.count(UsageRecord.id).label("calls"),
        func.coalesce(func.sum(UsageRecord.prompt_tokens), 0).label("prompt_tokens"),
        func.coalesce(func.sum(UsageRecord.completion_tokens), 0).label("completion_tokens"),
        func.coalesce(func.sum(UsageRecord.total_tokens), 0).label("total_tokens"),
        func.coalesce(func.sum(UsageRecord.cost_usd), 0.0).label("cost_usd"),
    )


async def stats_overview(session: AsyncSession) -> dict[str, Any]:
    row = (await session.execute(select(*_agg_cols()))).one()
    row_default = (await session.execute(
        select(*_agg_cols()).where(UsageRecord.is_default == True)  # noqa: E712
    )).one()
    failed = (await session.execute(
        select(func.count(UsageRecord.id)).where(UsageRecord.ok == False)  # noqa: E712
    )).scalar_one()
    return {
        "total": {
            "calls": row.calls, "prompt_tokens": row.prompt_tokens,
            "completion_tokens": row.completion_tokens, "total_tokens": row.total_tokens,
            "cost_usd": float(row.cost_usd),
        },
        "default_model": {
            "calls": row_default.calls, "prompt_tokens": row_default.prompt_tokens,
            "completion_tokens": row_default.completion_tokens,
            "total_tokens": row_default.total_tokens, "cost_usd": float(row_default.cost_usd),
        },
        "failed_calls": failed,
    }


async def stats_by_user(session: AsyncSession) -> list[dict[str, Any]]:
    stmt = (
        select(
            User.id, User.username, User.email,
            *_agg_cols(),
            func.coalesce(
                func.sum(
                    func.cast(UsageRecord.is_default, type_=__import__("sqlalchemy").Integer)
                    * UsageRecord.total_tokens
                ), 0
            ).label("default_tokens"),
        )
        .select_from(User)
        .join(UsageRecord, UsageRecord.user_id == User.id, isouter=True)
        .group_by(User.id, User.username, User.email)
        .order_by(func.coalesce(func.sum(UsageRecord.total_tokens), 0).desc())
    )
    rows = (await session.execute(stmt)).all()
    return [
        {
            "user_id": r.id, "username": r.username, "email": r.email,
            "calls": r.calls or 0,
            "prompt_tokens": r.prompt_tokens or 0,
            "completion_tokens": r.completion_tokens or 0,
            "total_tokens": r.total_tokens or 0,
            "default_tokens": int(r.default_tokens or 0),
            "cost_usd": float(r.cost_usd or 0.0),
        }
        for r in rows
    ]


async def stats_by_model(session: AsyncSession) -> list[dict[str, Any]]:
    stmt = (
        select(
            UsageRecord.model, UsageRecord.backend, UsageRecord.is_default,
            *_agg_cols(),
        )
        .group_by(UsageRecord.model, UsageRecord.backend, UsageRecord.is_default)
        .order_by(func.coalesce(func.sum(UsageRecord.total_tokens), 0).desc())
    )
    rows = (await session.execute(stmt)).all()
    return [
        {
            "model": r.model, "backend": r.backend, "is_default": bool(r.is_default),
            "calls": r.calls, "prompt_tokens": r.prompt_tokens,
            "completion_tokens": r.completion_tokens, "total_tokens": r.total_tokens,
            "cost_usd": float(r.cost_usd),
        }
        for r in rows
    ]


async def stats_for_task(task_id: str) -> dict[str, Any]:
    """按任务 ID 汇总 token 消耗，返回总量 + 按 agent 分项。"""
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(
            select(UsageRecord).where(UsageRecord.task_id == task_id)
            .order_by(UsageRecord.created_at)
        )).scalars().all()

    total_calls = len(rows)
    prompt_tokens = sum(r.prompt_tokens for r in rows)
    completion_tokens = sum(r.completion_tokens for r in rows)
    total_tokens = sum(r.total_tokens for r in rows)
    cost_usd = sum(r.cost_usd for r in rows)

    by_agent: dict[str, dict[str, Any]] = {}
    for r in rows:
        a = r.agent or "unknown"
        if a not in by_agent:
            by_agent[a] = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                           "total_tokens": 0, "cost_usd": 0.0, "model": r.model}
        by_agent[a]["calls"] += 1
        by_agent[a]["prompt_tokens"] += r.prompt_tokens
        by_agent[a]["completion_tokens"] += r.completion_tokens
        by_agent[a]["total_tokens"] += r.total_tokens
        by_agent[a]["cost_usd"] = round(by_agent[a]["cost_usd"] + r.cost_usd, 6)

    return {
        "task_id": task_id,
        "total": {
            "calls": total_calls,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "cost_usd": round(cost_usd, 6),
        },
        "by_agent": by_agent,
        "records": [
            {
                "agent": r.agent, "model": r.model, "backend": r.backend,
                "prompt_tokens": r.prompt_tokens, "completion_tokens": r.completion_tokens,
                "total_tokens": r.total_tokens, "cost_usd": r.cost_usd,
                "ok": r.ok, "error": r.error, "created_at": r.created_at,
            }
            for r in rows
        ],
    }


async def get_user_today_tokens(user_id: int) -> int:
    """查询用户今日 UTC 已消耗 total_tokens（含缓存，60s TTL）。"""
    now = time.time()
    cached = _quota_cache.get(user_id)
    if cached and cached[1] > now:
        return cached[0]
    # 今日 UTC 0 点时间戳
    import datetime
    today_start = datetime.datetime.utcnow().replace(
        hour=0, minute=0, second=0, microsecond=0
    ).timestamp()
    async with AsyncSessionLocal() as s:
        total = (await s.execute(
            select(func.coalesce(func.sum(UsageRecord.total_tokens), 0))
            .where(UsageRecord.user_id == user_id)
            .where(UsageRecord.created_at >= today_start)
            .where(UsageRecord.ok == True)  # noqa: E712
        )).scalar_one()
    result = int(total or 0)
    _quota_cache[user_id] = (result, now + _QUOTA_CACHE_TTL)
    return result


def invalidate_quota_cache(user_id: int | None = None) -> None:
    """配额变更后清除缓存。"""
    if user_id is None:
        _quota_cache.clear()
    else:
        _quota_cache.pop(user_id, None)


async def stats_for_user(session: AsyncSession, user_id: int, limit: int = 100) -> dict[str, Any]:
    total = (await session.execute(
        select(*_agg_cols()).where(UsageRecord.user_id == user_id)
    )).one()
    default = (await session.execute(
        select(*_agg_cols()).where(
            UsageRecord.user_id == user_id, UsageRecord.is_default == True  # noqa: E712
        )
    )).one()
    recent = (await session.execute(
        select(UsageRecord).where(UsageRecord.user_id == user_id)
        .order_by(UsageRecord.created_at.desc()).limit(limit)
    )).scalars().all()
    return {
        "total": {
            "calls": total.calls, "prompt_tokens": total.prompt_tokens,
            "completion_tokens": total.completion_tokens, "total_tokens": total.total_tokens,
            "cost_usd": float(total.cost_usd),
        },
        "default_model": {
            "calls": default.calls, "total_tokens": default.total_tokens,
            "cost_usd": float(default.cost_usd),
        },
        "recent": [
            {
                "id": r.id, "task_id": r.task_id, "agent": r.agent,
                "model": r.model, "backend": r.backend, "is_default": r.is_default,
                "prompt_tokens": r.prompt_tokens, "completion_tokens": r.completion_tokens,
                "total_tokens": r.total_tokens, "cost_usd": r.cost_usd,
                "ok": r.ok, "error": r.error, "created_at": r.created_at,
            }
            for r in recent
        ],
    }
