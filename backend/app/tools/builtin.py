"""内置工具：代码执行、文件读写、文件列表；Writer 学术搜索。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import aiofiles

from ..llm.schema import ToolSpec
from ..sandbox import JupyterSandbox
from ..tools.scholar import search_openalex, papers_to_prompt_text, search_arxiv, arxiv_to_prompt_text
from .base import Tool, ToolRegistry


def build_default_registry(sandbox: JupyterSandbox, work_dir: Path) -> ToolRegistry:
    reg = ToolRegistry()

    # ---- execute_python ----
    async def execute_python(code: str) -> dict[str, Any]:
        result = await sandbox.execute(code)
        return {
            "success": result.success,
            "stdout": result.stdout[-2000:],
            "stderr": result.stderr[-2000:],
            "text_result": result.text_result[:2000],
            "images": result.images,
            "error": result.error,
        }

    reg.register(
        Tool(
            spec=ToolSpec(
                name="execute_python",
                description=(
                    "在持久化 Jupyter Kernel 中执行 Python 代码。变量、import、状态在多次调用间保留。"
                    "用于数据分析、建模、绘图。绘图请使用 matplotlib/seaborn，调用 plt.show() 或 plt.savefig()。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "code": {"type": "string", "description": "要执行的 Python 代码"},
                    },
                    "required": ["code"],
                },
            ),
            handler=execute_python,
        )
    )

    # ---- write_file ----
    async def write_file(path: str, content: str) -> dict[str, Any]:
        wd_resolved = work_dir.resolve()
        target = (work_dir / path).resolve()
        if not (target == wd_resolved or target.is_relative_to(wd_resolved)):
            return {"success": False, "error": "path escape forbidden"}
        target.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(target, "w", encoding="utf-8") as f:
            await f.write(content)
        return {"success": True, "path": str(target), "bytes": len(content.encode("utf-8"))}

    reg.register(
        Tool(
            spec=ToolSpec(
                name="write_file",
                description="在工作区写入文本文件（覆盖）。用于保存中间产物如 markdown / json / py 脚本。",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "相对工作区的路径"},
                        "content": {"type": "string", "description": "文件内容"},
                    },
                    "required": ["path", "content"],
                },
            ),
            handler=write_file,
        )
    )

    # ---- read_file ----
    async def read_file(path: str, max_chars: int = 8000) -> dict[str, Any]:
        wd_resolved = work_dir.resolve()
        target = (work_dir / path).resolve()
        if not (target == wd_resolved or target.is_relative_to(wd_resolved)):
            return {"success": False, "error": "path escape forbidden"}
        if not target.exists():
            return {"success": False, "error": "file not found"}
        async with aiofiles.open(target, "r", encoding="utf-8", errors="replace") as f:
            content = await f.read()
        truncated = len(content) > max_chars
        return {
            "success": True,
            "content": content[:max_chars],
            "truncated": truncated,
            "total_chars": len(content),
        }

    reg.register(
        Tool(
            spec=ToolSpec(
                name="read_file",
                description="读取工作区中的文本文件。",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "max_chars": {"type": "integer", "default": 8000},
                    },
                    "required": ["path"],
                },
            ),
            handler=read_file,
        )
    )

    # ---- list_files ----
    async def list_files(subdir: str = ".") -> dict[str, Any]:
        wd_resolved = work_dir.resolve()
        target = (work_dir / subdir).resolve()
        if not (target == wd_resolved or target.is_relative_to(wd_resolved)) or not target.exists():
            return {"success": False, "files": []}
        entries = []
        for p in sorted(target.iterdir()):
            entries.append({
                "name": p.name,
                "type": "dir" if p.is_dir() else "file",
                "size": p.stat().st_size if p.is_file() else None,
            })
        return {"success": True, "files": entries}

    reg.register(
        Tool(
            spec=ToolSpec(
                name="list_files",
                description="列出工作区某子目录下的文件。",
                parameters={
                    "type": "object",
                    "properties": {
                        "subdir": {"type": "string", "default": "."},
                    },
                },
            ),
            handler=list_files,
        )
    )

    # ---- web_search（SearXNG 优先，fallback DuckDuckGo）----
    async def web_search(query: str, max_results: int = 6) -> dict[str, Any]:
        """联网搜索技术文档、库 API、算法实现参考。"""
        from ..services.search_service import search_web

        n = max(1, min(max_results, 10))
        try:
            results = await search_web(query, max_results=n)
        except Exception as e:
            return {"error": f"搜索失败: {e}", "results": []}

        if not results:
            return {"found": 0, "results": [], "note": "未找到相关结果"}

        return {
            "found": len(results),
            "results": [{"title": r.title, "url": r.url, "snippet": r.snippet} for r in results],
            "note": "已返回搜索结果，请参考文档/示例，需结合实际数据调整，勿直接复制。",
        }

    reg.register(Tool(
        spec=ToolSpec(
            name="web_search",
            description=(
                "联网搜索（SearXNG 优先，fallback DuckDuckGo），适合查找："
                "Python 库的 API 文档、特定函数用法示例、算法实现参考、报错信息解决方案。"
                "示例：web_search('scipy.optimize.minimize SLSQP constraints example')。"
                "注意：不用于学术文献（用 search_papers/search_arxiv），"
                "不用于通用知识（直接推理），仅在遇到具体 API 不确定时调用。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索词，英文效果更好"},
                    "max_results": {"type": "integer", "default": 6, "description": "返回条数（1-10）"},
                },
                "required": ["query"],
            },
        ),
        handler=web_search,
    ))

    return reg


def build_writer_registry(work_dir: Path, openalex_email: str = "") -> ToolRegistry:
    """Writer 专用工具集：文件读写 + OpenAlex 学术搜索。"""
    reg = ToolRegistry()

    # ---- read_file ----
    async def read_file(path: str, max_chars: int = 8000) -> dict[str, Any]:
        wd_resolved = work_dir.resolve()
        target = (work_dir / path).resolve()
        if not (target == wd_resolved or target.is_relative_to(wd_resolved)):
            return {"success": False, "error": "path escape forbidden"}
        if not target.exists():
            return {"success": False, "error": "file not found"}
        async with aiofiles.open(target, "r", encoding="utf-8", errors="replace") as f:
            content = await f.read()
        truncated = len(content) > max_chars
        return {"success": True, "content": content[:max_chars], "truncated": truncated}

    reg.register(Tool(
        spec=ToolSpec(
            name="read_file",
            description="读取工作区文件（Coder 产出的 result_qN.md、eda_report.md 等）。",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}, "max_chars": {"type": "integer", "default": 8000}},
                "required": ["path"],
            },
        ),
        handler=read_file,
    ))

    # ---- write_file ----
    async def write_file(path: str, content: str) -> dict[str, Any]:
        wd_resolved = work_dir.resolve()
        target = (work_dir / path).resolve()
        if not (target == wd_resolved or target.is_relative_to(wd_resolved)):
            return {"success": False, "error": "path escape forbidden"}
        target.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(target, "w", encoding="utf-8") as f:
            await f.write(content)
        return {"success": True, "path": str(target), "bytes": len(content.encode("utf-8"))}

    reg.register(Tool(
        spec=ToolSpec(
            name="write_file",
            description="在工作区写入文本文件（用于保存参考文献列表 references.bib 等）。",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
        ),
        handler=write_file,
    ))

    # ---- search_papers ----
    async def search_papers(query: str, limit: int = 8) -> dict[str, Any]:
        """通过 OpenAlex 搜索学术文献，返回格式化的引用条目。"""
        papers = await search_openalex(query, email=openalex_email, limit=limit)
        if not papers:
            return {"found": 0, "papers": "", "note": "未检索到相关文献或 OPENALEX_EMAIL 未配置"}
        return {
            "found": len(papers),
            "papers": papers_to_prompt_text(papers),
            "note": f"已检索 {len(papers)} 篇文献，请将引用格式插入正文 [^N] 脚注并在文末列出参考文献。",
        }

    reg.register(Tool(
        spec=ToolSpec(
            name="search_papers",
            description=(
                "搜索 OpenAlex 学术文献数据库。当论文需要引用相关理论文献时调用，"
                "传入中英文关键词（如 'AHP TOPSIS decision making', 'XGBoost regression prediction'）。"
                "返回含 GB/T 7714 格式引用条目的文献列表。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词（英文效果更好）"},
                    "limit": {"type": "integer", "default": 8, "description": "返回文献数量（1-15）"},
                },
                "required": ["query"],
            },
        ),
        handler=search_papers,
    ))

    # ---- search_arxiv ----
    async def _search_arxiv(query: str, limit: int = 6) -> dict[str, Any]:
        """搜索 arXiv 预印本，无需邮箱，始终可用。"""
        papers = await search_arxiv(query, limit=limit)
        if not papers:
            return {"found": 0, "papers": "", "note": "arXiv 未检索到相关预印本"}
        return {
            "found": len(papers),
            "papers": arxiv_to_prompt_text(papers),
            "note": f"已检索 {len(papers)} 篇 arXiv 预印本，请将引用格式插入正文并在文末列出。",
        }

    reg.register(Tool(
        spec=ToolSpec(
            name="search_arxiv",
            description=(
                "搜索 arXiv 预印本数据库，无需配置邮箱，始终可用。"
                "适合检索最新算法、模型和方法论文（如 'graph neural network traffic prediction'）。"
                "与 search_papers(OpenAlex) 互补：arXiv 偏新，OpenAlex 偏经典。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "英文关键词效果最佳"},
                    "limit": {"type": "integer", "default": 6, "description": "返回数量（1-15）"},
                },
                "required": ["query"],
            },
        ),
        handler=_search_arxiv,
    ))

    return reg
