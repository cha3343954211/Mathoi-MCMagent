"""内置工具：代码执行、文件读写、文件列表。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import aiofiles

from ..llm.schema import ToolSpec
from ..sandbox import JupyterSandbox
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
