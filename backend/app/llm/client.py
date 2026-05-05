"""LLM 客户端：按用户解析生效配置 + 计量记录。

- `chat_for_user(user_id, agent, ...)`：主入口，自动解析配置并记录 usage
- 配置来源：model_configs 表（user 自定义 或 全局默认）
"""
from __future__ import annotations

from typing import Any, AsyncIterator, Optional

from sqlalchemy import select
from tenacity import AsyncRetrying, stop_after_attempt, wait_exponential

from ..core.events import EventType
from ..core.events import emit as emit_event
from ..core.logging import logger
from ..db import AsyncSessionLocal, User
from ..services.model_service import ResolvedConfig, resolve_effective
from ..services.usage_service import record_usage
from .schema import ChatMessage, ToolSpec


class LLMError(RuntimeError):
    pass


async def _get_user(user_id: int) -> Optional[User]:
    async with AsyncSessionLocal() as s:
        return (await s.execute(select(User).where(User.id == user_id))).scalar_one_or_none()


async def _resolve(user_id: int, agent: str) -> ResolvedConfig:
    user = await _get_user(user_id)
    if not user:
        raise LLMError(f"user {user_id} not found")
    async with AsyncSessionLocal() as s:
        return await resolve_effective(s, user=user, agent=agent)


def _compute_cost(cfg: ResolvedConfig, pt: int, ct: int) -> float:
    return round(cfg.price_prompt_per_1k * pt / 1000 + cfg.price_completion_per_1k * ct / 1000, 6)


async def chat_for_user(
    *,
    user_id: int,
    agent: str,
    messages: list[ChatMessage],
    task_id: Optional[str] = None,
    tools: Optional[list[ToolSpec]] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    response_format: Optional[dict[str, Any]] = None,
) -> ChatMessage:
    cfg = await _resolve(user_id, agent)
    if not cfg.model or not cfg.api_key:
        err = f"LLM 未配置：agent={agent} is_default={cfg.is_default}"
        await record_usage(
            user_id=user_id, task_id=task_id, agent=agent, backend=cfg.backend,
            model=cfg.model or "", is_default=cfg.is_default, ok=False, error=err,
        )
        raise LLMError(err)

    params: dict[str, Any] = {
        "model": cfg.model,
        "messages": [m.to_openai() for m in messages],
        "temperature": temperature if temperature is not None else cfg.temperature,
    }
    if tools:
        params["tools"] = [t.to_openai() for t in tools]
        params["tool_choice"] = "auto"
    effective_max = max_tokens or cfg.max_tokens
    if effective_max:
        params["max_tokens"] = effective_max
    if response_format:
        params["response_format"] = response_format

    last_err: Optional[Exception] = None
    resp: dict[str, Any] = {}
    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=False,
    ):
        with attempt:
            try:
                if cfg.backend == "litellm":
                    resp = await _call_litellm(cfg, params)
                else:
                    resp = await _call_openai(cfg, params)
            except Exception as e:
                last_err = e
                raise
    if not resp:
        err = f"LLM 调用失败：{last_err}"
        await record_usage(
            user_id=user_id, task_id=task_id, agent=agent, backend=cfg.backend,
            model=cfg.model, is_default=cfg.is_default, ok=False, error=str(last_err),
        )
        raise LLMError(err)

    msg = resp["choices"][0]["message"]
    usage = resp.get("usage") or {}
    pt = int(usage.get("prompt_tokens", 0) or 0)
    ct = int(usage.get("completion_tokens", 0) or 0)
    cost = _compute_cost(cfg, pt, ct)
    await record_usage(
        user_id=user_id, task_id=task_id, agent=agent, backend=cfg.backend,
        model=cfg.model, is_default=cfg.is_default,
        prompt_tokens=pt, completion_tokens=ct,
        cost_usd=cost, ok=True,
    )
    # 实时推送 token 计量事件
    if task_id:
        try:
            await emit_event(
                EventType.AGENT_LLM_USAGE, task_id, agent=agent,
                model=cfg.model, backend=cfg.backend, is_default=cfg.is_default,
                prompt_tokens=pt, completion_tokens=ct,
                total_tokens=pt + ct, cost_usd=cost,
            )
        except Exception:
            pass
    return ChatMessage(
        role="assistant",
        content=msg.get("content") or "",
        tool_calls=msg.get("tool_calls"),
    )


async def stream_for_user(
    *,
    user_id: int,
    agent: str,
    messages: list[ChatMessage],
    temperature: Optional[float] = None,
) -> AsyncIterator[str]:
    cfg = await _resolve(user_id, agent)
    if not cfg.model or not cfg.api_key:
        raise LLMError(f"LLM 未配置：agent={agent}")
    params: dict[str, Any] = {
        "model": cfg.model,
        "messages": [m.to_openai() for m in messages],
        "temperature": temperature if temperature is not None else cfg.temperature,
        "stream": True,
    }
    if cfg.backend == "litellm":
        async for chunk in _stream_litellm(cfg, params):
            yield chunk
    else:
        async for chunk in _stream_openai(cfg, params):
            yield chunk


# ---------- 后端实现 ----------
async def _call_openai(cfg: ResolvedConfig, params: dict[str, Any]) -> dict[str, Any]:
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=cfg.api_key, base_url=cfg.base_url or None)
    resp = await client.chat.completions.create(**params)
    return resp.model_dump()


async def _stream_openai(cfg: ResolvedConfig, params: dict[str, Any]) -> AsyncIterator[str]:
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=cfg.api_key, base_url=cfg.base_url or None)
    stream = await client.chat.completions.create(**params)
    async for chunk in stream:
        delta = chunk.choices[0].delta
        if delta and delta.content:
            yield delta.content


async def _call_litellm(cfg: ResolvedConfig, params: dict[str, Any]) -> dict[str, Any]:
    import litellm
    if cfg.base_url:
        params["api_base"] = cfg.base_url
    if cfg.api_key:
        params["api_key"] = cfg.api_key
    resp = await litellm.acompletion(**params)
    return resp.model_dump() if hasattr(resp, "model_dump") else dict(resp)


async def _stream_litellm(cfg: ResolvedConfig, params: dict[str, Any]) -> AsyncIterator[str]:
    import litellm
    if cfg.base_url:
        params["api_base"] = cfg.base_url
    if cfg.api_key:
        params["api_key"] = cfg.api_key
    stream = await litellm.acompletion(**params)
    async for chunk in stream:
        try:
            delta = chunk.choices[0].delta
            content = getattr(delta, "content", None)
            if content:
                yield content
        except Exception:
            continue


def reset_llm_cache() -> None:
    """保留兼容：当前实现按需解析，无需清理。"""
    logger.debug("reset_llm_cache called (noop in per-user mode)")
