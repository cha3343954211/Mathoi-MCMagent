"""
OpenAlex 学术文献搜索工具（Writer 使用）。
参考 MathModelAgent-main 实现，集成到本项目工具体系。
无 email 时静默跳过（不抛错）。
"""
from __future__ import annotations

import asyncio
import json
from functools import partial
from typing import Any

import httpx

from ..core.logging import logger

_BASE_URL = "https://api.openalex.org"
_SELECT_FIELDS = "id,title,display_name,authorships,cited_by_count,doi,publication_year,biblio,abstract_inverted_index"


def _rebuild_abstract(inverted: dict) -> str:
    """从 OpenAlex 倒排索引重建摘要文本。"""
    if not inverted:
        return ""
    max_pos = max((p for positions in inverted.values() for p in positions), default=0)
    words = [""] * (max_pos + 1)
    for word, positions in inverted.items():
        for p in positions:
            words[p] = word
    return " ".join(words).strip()


def _format_citation_gb(work: dict) -> str:
    """GB/T 7714-2015 格式引用条目。"""
    authors = [
        a.get("author", {}).get("display_name", "")
        for a in work.get("authorships", [])
        if a.get("author")
    ]
    if len(authors) > 3:
        authors_str = f"{authors[0]}, et al."
    elif authors:
        authors_str = ", ".join(authors)
    else:
        authors_str = "Unknown"

    title = work.get("display_name") or work.get("title", "")
    year = work.get("publication_year", "")
    doi = work.get("doi", "")
    biblio = work.get("biblio", {})
    vol = biblio.get("volume", "")
    issue = biblio.get("issue", "")
    pages = ""
    if biblio.get("first_page") and biblio.get("last_page"):
        pages = f"{biblio['first_page']}-{biblio['last_page']}"

    parts = [f"{authors_str}."]
    parts.append(f" {title}[J/OL].")
    if year:
        parts.append(f" {year}")
    if vol:
        parts.append(f", {vol}")
    if issue:
        parts.append(f"({issue})")
    if pages:
        parts.append(f":{pages}")
    if doi:
        parts.append(f". DOI: {doi}")
    return "".join(parts).strip()


async def search_openalex(query: str, email: str | None = None, limit: int = 8) -> list[dict[str, Any]]:
    """
    异步搜索 OpenAlex，返回格式化的文献列表。
    email 必须提供（polite pool），否则返回空列表并打印警告。
    """
    if not email:
        logger.warning("OpenAlex search skipped: no OPENALEX_EMAIL configured")
        return []

    params: dict[str, Any] = {
        "search": query,
        "per_page": limit,
        "select": _SELECT_FIELDS,
        "mailto": email,
    }
    headers = {"User-Agent": f"MathoiAgent/1.0 (mailto:{email})"}

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(f"{_BASE_URL}/works", params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("OpenAlex request failed: {}", exc)
        return []

    results: list[dict] = []
    for work in data.get("results", []):
        results.append({
            "title": work.get("display_name") or work.get("title", ""),
            "abstract": _rebuild_abstract(work.get("abstract_inverted_index", {})),
            "authors": [
                a.get("author", {}).get("display_name", "")
                for a in work.get("authorships", [])
                if a.get("author")
            ],
            "year": work.get("publication_year"),
            "doi": work.get("doi", ""),
            "citations": work.get("cited_by_count", 0),
            "citation_gb": _format_citation_gb(work),
        })
    return results


def papers_to_prompt_text(papers: list[dict]) -> str:
    """将文献列表格式化为 Writer 可直接使用的文本（含 GB/T 引用格式）。"""
    if not papers:
        return "（未检索到相关文献）"
    lines = []
    for i, p in enumerate(papers, 1):
        abstract_short = p["abstract"][:300] + "…" if len(p["abstract"]) > 300 else p["abstract"]
        lines.append(
            f"[{i}] {p['citation_gb']}\n"
            f"    摘要：{abstract_short}\n"
            f"    引用次数：{p['citations']}"
        )
    return "\n\n".join(lines)
