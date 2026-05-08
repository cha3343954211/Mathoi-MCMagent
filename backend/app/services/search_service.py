"""联网搜索服务：优先 SearXNG，失败后 fallback 到 DuckDuckGo。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal

import httpx
from loguru import logger

from ..core.config import settings


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


# ── SearXNG ───────────────────────────────────────────────────────────────────

async def _search_searxng(
    query: str,
    max_results: int,
    base_url: str,
    timeout: float,
) -> list[SearchResult]:
    """调用本地 SearXNG JSON API。"""
    params = {
        "q": query,
        "format": "json",
        "language": "auto",
        "pageno": 1,
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(f"{base_url.rstrip('/')}/search", params=params)
        resp.raise_for_status()
        data = resp.json()

    results: list[SearchResult] = []
    for item in data.get("results", [])[:max_results]:
        results.append(SearchResult(
            title=item.get("title", ""),
            url=item.get("url", ""),
            snippet=(item.get("content") or item.get("description") or "")[:400],
        ))
    return results


# ── DuckDuckGo ────────────────────────────────────────────────────────────────

async def _search_duckduckgo(
    query: str,
    max_results: int,
) -> list[SearchResult]:
    """通过 duckduckgo-search 包搜索，在线程池中执行（同步 SDK）。"""
    try:
        from duckduckgo_search import DDGS  # type: ignore
    except ImportError:
        raise RuntimeError("duckduckgo-search 未安装，请 pip install duckduckgo-search")

    def _sync() -> list[dict]:
        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=max_results))

    raw = await asyncio.get_event_loop().run_in_executor(None, _sync)
    return [
        SearchResult(
            title=r.get("title", ""),
            url=r.get("href", ""),
            snippet=(r.get("body") or "")[:400],
        )
        for r in raw
    ]


# ── 公共入口 ──────────────────────────────────────────────────────────────────

async def _load_db_config() -> dict[str, str]:
    """从 DB 读取搜索配置（优先于 env），失败时返回空 dict。"""
    try:
        from ..db import AsyncSessionLocal, SystemSetting
        from sqlalchemy import select
        _KEYS = {"search_provider", "searxng_base_url", "searxng_timeout", "search_max_results"}
        async with AsyncSessionLocal() as session:
            rows = (await session.execute(
                select(SystemSetting).where(SystemSetting.key.in_(_KEYS))
            )).scalars().all()
            return {r.key: r.value for r in rows}
    except Exception as exc:
        logger.debug("search_service: DB config load failed ({}), use env", exc)
        return {}


async def search_web(
    query: str,
    max_results: int | None = None,
    provider: Literal["duckduckgo", "searxng", "auto"] = "auto",
) -> list[SearchResult]:
    """
    统一搜索入口。

    优先级：DB 配置 > env 配置。
    SearXNG 失败自动 fallback 到 DuckDuckGo。
    """
    db = await _load_db_config()

    effective_provider = db.get("search_provider") or settings.search_provider
    effective_base_url  = db.get("searxng_base_url") or settings.searxng_base_url
    effective_timeout   = float(db.get("searxng_timeout") or settings.searxng_timeout)
    n = max_results or int(db.get("search_max_results") or settings.search_max_results)

    if provider != "auto":
        effective_provider = provider

    if effective_provider == "searxng" and effective_base_url:
        try:
            results = await _search_searxng(query, n, effective_base_url, effective_timeout)
            logger.debug("SearXNG returned {} results for: {}", len(results), query)
            return results
        except Exception as exc:
            logger.warning("SearXNG failed ({}), fallback to DuckDuckGo", exc)

    results = await _search_duckduckgo(query, n)
    logger.debug("DuckDuckGo returned {} results for: {}", len(results), query)
    return results


async def test_searxng(base_url: str, timeout: float = 8.0) -> dict:
    """连通性探针，供管理员页面「测试连接」使用。"""
    try:
        results = await _search_searxng("python", 3, base_url, timeout)
        return {"ok": True, "found": len(results), "base_url": base_url}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "base_url": base_url}
