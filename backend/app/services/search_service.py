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

async def search_web(
    query: str,
    max_results: int | None = None,
    provider: Literal["duckduckgo", "searxng", "auto"] = "auto",
) -> list[SearchResult]:
    """
    统一搜索入口。

    provider="auto" 时优先读 settings.search_provider。
    SearXNG 失败自动 fallback 到 DuckDuckGo。
    """
    n = max_results or settings.search_max_results

    effective = settings.search_provider if provider == "auto" else provider

    if effective == "searxng" and settings.searxng_base_url:
        try:
            results = await _search_searxng(
                query, n,
                settings.searxng_base_url,
                settings.searxng_timeout,
            )
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
