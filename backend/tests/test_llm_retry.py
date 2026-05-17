"""LLM 客户端：错误分类、熔断器、Retry-After 解析。"""
from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from app.llm.client import (
    _CB_THRESHOLD,
    LLMError,
    _cb_check,
    _cb_record,
    _circuit_breaker,
    _is_transient,
    _retry_after_seconds,
)


def test_is_transient_network_errors():
    assert _is_transient(httpx.TimeoutException("slow"))
    assert _is_transient(httpx.ConnectError("nope"))
    assert _is_transient(asyncio.TimeoutError())
    assert _is_transient(TimeoutError())


def test_is_transient_status_codes():
    class _E(Exception):
        def __init__(self, code: int):
            self.status_code = code

    assert _is_transient(_E(429))
    assert _is_transient(_E(500))
    assert _is_transient(_E(502))
    assert _is_transient(_E(503))
    assert _is_transient(_E(504))
    assert not _is_transient(_E(400))
    assert not _is_transient(_E(401))
    assert not _is_transient(_E(403))


def test_is_transient_plain_exception_not_retried():
    assert not _is_transient(ValueError("bad input"))
    assert not _is_transient(RuntimeError("oops"))


def test_retry_after_seconds_parses_header():
    class _Resp:
        def __init__(self, headers):
            self.headers = headers

    class _E(Exception):
        def __init__(self, headers):
            self.response = _Resp(headers)

    assert _retry_after_seconds(_E({"retry-after": "5"})) == 5.0
    assert _retry_after_seconds(_E({"Retry-After": "12.5"})) == 12.5
    assert _retry_after_seconds(_E({})) == 0.0
    assert _retry_after_seconds(ValueError("no response attr")) == 0.0


def test_circuit_breaker_opens_on_threshold():
    """连续失败达到阈值后，下次 _cb_check 抛 LLMError。"""
    user_id = 999
    agent = "test_agent"
    _circuit_breaker.pop((user_id, agent), None)

    # 先 N-1 次失败：尚未熔断
    for _ in range(_CB_THRESHOLD - 1):
        _cb_record(user_id, agent, success=False)
        _cb_check(user_id, agent)   # 不应抛

    # 第 N 次失败：触发熔断
    _cb_record(user_id, agent, success=False)
    with pytest.raises(LLMError) as exc:
        _cb_check(user_id, agent)
    assert "熔断" in str(exc.value)


def test_circuit_breaker_resets_on_success():
    user_id = 998
    agent = "test_agent"
    _circuit_breaker.pop((user_id, agent), None)
    for _ in range(_CB_THRESHOLD):
        _cb_record(user_id, agent, success=False)
    # 成功一次应清空熔断状态
    _cb_record(user_id, agent, success=True)
    _cb_check(user_id, agent)   # 不应抛


def test_circuit_breaker_window_resets_old_failures():
    """旧失败超出 _CB_WINDOW 后不再累计。"""
    user_id = 997
    agent = "test_agent"
    # 手动塞一个很久之前的窗口起点
    _circuit_breaker[(user_id, agent)] = (_CB_THRESHOLD - 1, time.time() - 3600, 0.0)
    # 当前一次失败应触发窗口重置 → count=1，未到阈值
    _cb_record(user_id, agent, success=False)
    _cb_check(user_id, agent)   # 不应抛
    state = _circuit_breaker[(user_id, agent)]
    assert state[0] == 1
