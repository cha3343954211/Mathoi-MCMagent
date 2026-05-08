"""LLM 客户端：按用户解析生效配置 + 计量记录。

- `chat_for_user(user_id, agent, ...)`：主入口，自动解析配置并记录 usage
- 配置来源：model_configs 表（user 自定义 或 全局默认）
"""
from __future__ import annotations

from typing import Any, AsyncIterator, Optional

from sqlalchemy import select
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential

from ..core.events import EventType
from ..core.events import emit as emit_event
from ..core.logging import logger
from ..db import AsyncSessionLocal, User
from ..services.model_service import ResolvedConfig, resolve_effective
from ..services.usage_service import record_usage
from .schema import ChatMessage, ToolSpec


class LLMError(RuntimeError):
    pass


# ---------- 错误分类 ----------
def _is_transient(exc: Exception) -> bool:
    """只对可重试的瞬时错误重试（网络、限速、服务端 5xx）。
    401/403/400 等致命错误立即失败，不浪费重试次数。
    """
    # openai SDK 具名异常
    try:
        from openai import (
            RateLimitError, APITimeoutError, APIConnectionError, InternalServerError,
        )
        if isinstance(exc, (RateLimitError, APITimeoutError, APIConnectionError)):
            return True
        if isinstance(exc, InternalServerError):
            return True
    except ImportError:
        pass
    # httpx 网络层
    try:
        import httpx
        if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError,
                            httpx.RemoteProtocolError, httpx.NetworkError)):
            return True
    except ImportError:
        pass
    # 通用：HTTP 状态码
    status = getattr(exc, "status_code", None)
    if status in (429, 500, 502, 503, 504):
        return True
    return False


def _retry_after_seconds(exc: Exception) -> float:
    """从 429 响应中提取 Retry-After 头（秒），不存在则返回 0。"""
    try:
        headers = getattr(getattr(exc, "response", None), "headers", {}) or {}
        val = headers.get("retry-after") or headers.get("Retry-After") or ""
        if val:
            return max(0.0, float(val))
    except Exception:
        pass
    return 0.0


# ---------- 简单熔断器 ----------
# key: (user_id, agent)  value: (fail_count, window_start, open_until)
_circuit_breaker: dict[tuple[int, str], tuple[int, float, float]] = {}
_CB_THRESHOLD = 3       # 窗口内连续失败 N 次触发
_CB_WINDOW = 60.0       # 统计窗口（秒）
_CB_OPEN_SECS = 60.0    # 熔断打开后的冷却时长（秒）


def _cb_check(user_id: int, agent: str) -> None:
    """如果熔断器处于 open 状态，直接抛出 LLMError（不发起 LLM 请求）。"""
    import time
    key = (user_id, agent)
    state = _circuit_breaker.get(key)
    if not state:
        return
    _, _, open_until = state
    if open_until > time.time():
        remaining = int(open_until - time.time())
        raise LLMError(f"LLM 熔断中（{agent}），请 {remaining}s 后重试。")


def _cb_record(user_id: int, agent: str, success: bool) -> None:
    """记录一次调用结果，更新熔断器状态。"""
    import time
    key = (user_id, agent)
    if success:
        _circuit_breaker.pop(key, None)
        return
    now = time.time()
    state = _circuit_breaker.get(key, (0, now, 0.0))
    count, window_start, open_until = state
    # 窗口过期则重置
    if now - window_start > _CB_WINDOW:
        count, window_start = 0, now
    count += 1
    if count >= _CB_THRESHOLD:
        logger.warning("LLM 熔断触发: agent={} user={} fails={}", agent, user_id, count)
        _circuit_breaker[key] = (count, window_start, now + _CB_OPEN_SECS)
    else:
        _circuit_breaker[key] = (count, window_start, 0.0)


async def _get_user(user_id: int) -> Optional[User]:
    async with AsyncSessionLocal() as s:
        return (await s.execute(select(User).where(User.id == user_id))).scalar_one_or_none()


# LLM 配置解析缓存：(user_id, agent) → (ResolvedConfig, expire_ts)
_resolve_cache: dict[tuple[int, str], tuple["ResolvedConfig", float]] = {}
_RESOLVE_TTL = 30.0   # 秒
_MAX_RESOLVE_CACHE = 200   # 防止内存漏海


async def _resolve(user_id: int, agent: str) -> ResolvedConfig:
    import time
    key = (user_id, agent)
    cached = _resolve_cache.get(key)
    if cached and cached[1] > time.time():
        return cached[0]
    user = await _get_user(user_id)
    if not user:
        raise LLMError(f"user {user_id} not found")
    async with AsyncSessionLocal() as s:
        cfg = await resolve_effective(s, user=user, agent=agent)
    now = time.time()
    _resolve_cache[key] = (cfg, now + _RESOLVE_TTL)
    # 超限则先清过期条目，再淘汰最早的
    if len(_resolve_cache) > _MAX_RESOLVE_CACHE:
        expired = [k for k, v in _resolve_cache.items() if v[1] <= now]
        for k in expired:
            del _resolve_cache[k]
        if len(_resolve_cache) > _MAX_RESOLVE_CACHE:
            oldest = sorted(_resolve_cache, key=lambda k: _resolve_cache[k][1])
            for k in oldest[: len(_resolve_cache) - _MAX_RESOLVE_CACHE]:
                del _resolve_cache[k]
    return cfg


def invalidate_resolve_cache(user_id: int | None = None) -> None:
    """配置变更时主动清除缓存（model_service 在更新配置后调用）。"""
    if user_id is None:
        _resolve_cache.clear()
    else:
        for k in list(_resolve_cache.keys()):
            if k[0] == user_id:
                del _resolve_cache[k]


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

    # 熔断器检查（open 状态直接抛出，不发 LLM 请求）
    _cb_check(user_id, agent)

    last_err: Optional[Exception] = None
    resp: dict[str, Any] = {}
    # task_id 存在时走内部流式调用，发射 stream_chunk 事件
    use_stream = bool(task_id)
    _attempt_no = 0

    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=2, min=2, max=60),
        retry=retry_if_exception(_is_transient),   # 只重试瞬时错误
        reraise=True,
    ):
        with attempt:
            _attempt_no += 1
            # 429 时追加 Retry-After 等待（在 tenacity 退避之外）
            if last_err is not None and _attempt_no > 1:
                ra = _retry_after_seconds(last_err)
                if ra > 0:
                    logger.info("LLM 429 Retry-After={}s | agent={}", ra, agent)
                    import asyncio as _aio
                    await _aio.sleep(min(ra, 120))
            try:
                if cfg.backend == "litellm":
                    resp = await (
                        _call_litellm_streaming(cfg, params, task_id, agent)
                        if use_stream else _call_litellm(cfg, params)
                    )
                else:
                    resp = await (
                        _call_openai_streaming(cfg, params, task_id, agent)
                        if use_stream else _call_openai(cfg, params)
                    )
            except Exception as e:
                last_err = e
                if _attempt_no > 1:
                    logger.warning("LLM retry {}/{} failed: {} | agent={} user={}",
                                   _attempt_no, 4, type(e).__name__, agent, user_id)
                raise

    if not resp:
        err = f"LLM 调用失败：{last_err}"
        _cb_record(user_id, agent, success=False)
        await record_usage(
            user_id=user_id, task_id=task_id, agent=agent, backend=cfg.backend,
            model=cfg.model, is_default=cfg.is_default, ok=False, error=str(last_err),
        )
        raise LLMError(err)

    _cb_record(user_id, agent, success=True)
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
    client = AsyncOpenAI(api_key=cfg.api_key, base_url=cfg.base_url or None, timeout=120.0)
    resp = await client.chat.completions.create(**params)
    return resp.model_dump()


async def _stream_chunks_openai(
    client: Any, params: dict[str, Any],
    task_id: Optional[str], agent_name: str,
) -> dict[str, Any]:
    """执行一次流式调用并收集结果。params 中已含 stream=True 等参数。"""
    full_content = ""
    tool_calls_acc: dict[int, dict] = {}
    usage_obj = None

    stream = await client.chat.completions.create(**params)
    async for chunk in stream:
        if getattr(chunk, "usage", None):
            usage_obj = chunk.usage
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta.content:
            full_content += delta.content
            if task_id:
                await emit_event(EventType.AGENT_STREAM_CHUNK, task_id,
                                 agent=agent_name, delta=delta.content)
        for tc in (getattr(delta, "tool_calls", None) or []):
            i = tc.index
            if i not in tool_calls_acc:
                tool_calls_acc[i] = {"id": "", "type": "function",
                                     "function": {"name": "", "arguments": ""}}
            if tc.id:
                tool_calls_acc[i]["id"] = tc.id
            if tc.function:
                if tc.function.name:
                    tool_calls_acc[i]["function"]["name"] += tc.function.name
                if tc.function.arguments:
                    tool_calls_acc[i]["function"]["arguments"] += tc.function.arguments

    tool_calls = [tool_calls_acc[i] for i in sorted(tool_calls_acc)] if tool_calls_acc else None
    return {
        "choices": [{"message": {
            "role": "assistant",
            "content": full_content or None,
            "tool_calls": tool_calls,
        }}],
        "usage": usage_obj.model_dump() if usage_obj else {},
    }


async def _call_openai_streaming(
    cfg: ResolvedConfig, params: dict[str, Any],
    task_id: Optional[str], agent_name: str,
) -> dict[str, Any]:
    """内部流式调用：逐 chunk 发射 AGENT_STREAM_CHUNK 事件，返回与 _call_openai 相同结构。
    自动降级：若提供商不支持 stream_options 则去掉该参数重试。
    """
    from openai import AsyncOpenAI, BadRequestError
    client = AsyncOpenAI(api_key=cfg.api_key, base_url=cfg.base_url or None, timeout=120.0)
    # 先尝试带 include_usage 的流（标准 OpenAI 支持）
    try:
        return await _stream_chunks_openai(
            client,
            {**params, "stream": True, "stream_options": {"include_usage": True}},
            task_id, agent_name,
        )
    except (BadRequestError, Exception) as e:
        # 仅在参数错误时降级，其他异常继续抛出
        err_str = str(e).lower()
        if "stream_options" not in err_str and "400" not in err_str:
            raise
        logger.debug("stream_options not supported by provider, retrying without it")
    # 降级：不带 stream_options
    return await _stream_chunks_openai(
        client,
        {**params, "stream": True},
        task_id, agent_name,
    )


async def _stream_openai(cfg: ResolvedConfig, params: dict[str, Any]) -> AsyncIterator[str]:
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=cfg.api_key, base_url=cfg.base_url or None, timeout=120.0)
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


async def _call_litellm_streaming(
    cfg: ResolvedConfig, params: dict[str, Any],
    task_id: Optional[str], agent_name: str,
) -> dict[str, Any]:
    import litellm
    p = {**params, "stream": True}
    if cfg.base_url:
        p["api_base"] = cfg.base_url
    if cfg.api_key:
        p["api_key"] = cfg.api_key

    stream = await litellm.acompletion(**p)
    full_content = ""
    tool_calls_acc: dict[int, dict] = {}
    usage_obj = None

    async for chunk in stream:
        try:
            if getattr(chunk, "usage", None):
                usage_obj = chunk.usage
            if not getattr(chunk, "choices", None):
                continue
            delta = chunk.choices[0].delta
            content = getattr(delta, "content", None) or ""
            if content:
                full_content += content
                if task_id:
                    await emit_event(EventType.AGENT_STREAM_CHUNK, task_id,
                                     agent=agent_name, delta=content)
            for tc in (getattr(delta, "tool_calls", None) or []):
                i = getattr(tc, "index", 0)
                if i not in tool_calls_acc:
                    tool_calls_acc[i] = {"id": "", "type": "function",
                                         "function": {"name": "", "arguments": ""}}
                if getattr(tc, "id", None):
                    tool_calls_acc[i]["id"] = tc.id
                fn = getattr(tc, "function", None)
                if fn:
                    tool_calls_acc[i]["function"]["name"] += getattr(fn, "name", "") or ""
                    tool_calls_acc[i]["function"]["arguments"] += getattr(fn, "arguments", "") or ""
        except Exception:
            continue

    tool_calls = [tool_calls_acc[i] for i in sorted(tool_calls_acc)] if tool_calls_acc else None
    # litellm usage 对象统一安全转 dict
    usage_dict: dict[str, Any] = {}
    if usage_obj is not None:
        if hasattr(usage_obj, "model_dump"):
            usage_dict = usage_obj.model_dump()
        elif hasattr(usage_obj, "__dict__"):
            usage_dict = {k: v for k, v in usage_obj.__dict__.items() if not k.startswith("_")}
    return {
        "choices": [{"message": {
            "role": "assistant",
            "content": full_content or None,
            "tool_calls": tool_calls,
        }}],
        "usage": usage_dict,
    }


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
