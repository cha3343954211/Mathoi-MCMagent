"""内置工具：代码执行、文件读写、文件列表；Writer 学术搜索。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import aiofiles

from ..llm.schema import ToolSpec
from ..sandbox import JupyterSandbox
from ..tools.scholar import search_openalex, papers_to_prompt_text
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
        target = (work_dir / path).resolve()
        if not str(target).startswith(str(work_dir.resolve())):
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
        target = (work_dir / path).resolve()
        if not str(target).startswith(str(work_dir.resolve())):
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
        target = (work_dir / subdir).resolve()
        if not str(target).startswith(str(work_dir.resolve())) or not target.exists():
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

    return reg


def build_writer_registry(work_dir: Path, openalex_email: str = "") -> ToolRegistry:
    """Writer 专用工具集：文件读写 + OpenAlex 学术搜索。"""
    reg = ToolRegistry()

    # ---- read_file ----
    async def read_file(path: str, max_chars: int = 8000) -> dict[str, Any]:
        target = (work_dir / path).resolve()
        if not str(target).startswith(str(work_dir.resolve())):
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
        target = (work_dir / path).resolve()
        if not str(target).startswith(str(work_dir.resolve())):
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

    return reg
