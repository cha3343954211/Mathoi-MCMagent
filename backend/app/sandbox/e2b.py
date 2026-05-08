"""E2B 云端沙箱（可选后端）。

依赖 e2b-code-interpreter，安装方式：
    pip install e2b-code-interpreter

与 JupyterSandbox 保持相同的公共接口，以便 orchestrator 无感切换。

主要差异：
- 代码在 E2B 远程容器执行，网络隔离、安全性更高
- 数据文件在任务开始时上传，生成图片在执行后下载
- 不需要本地 Jupyter Kernel 进程

使用方式（.env）：
    SANDBOX_KIND=e2b
    E2B_API_KEY=e2b_xxx
"""
from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Any, Optional

from ..core.config import get_settings
from ..core.events import EventType, emit
from ..core.logging import logger
from .notebook import NotebookRecorder

try:
    from e2b_code_interpreter import AsyncSandbox as _E2BSandbox  # type: ignore[import]
    _E2B_AVAILABLE = True
except ImportError:
    _E2B_AVAILABLE = False
    _E2BSandbox = None  # type: ignore[assignment,misc]


# 复用 JupyterSandbox 的 ExecResult 结构
from .jupyter import ExecResult


class E2BSandbox:
    """E2B 云端沙箱，接口与 JupyterSandbox 保持一致。"""

    def __init__(self, task_id: str, work_dir: Path) -> None:
        if not _E2B_AVAILABLE:
            raise RuntimeError(
                "e2b-code-interpreter 未安装，请运行: pip install e2b-code-interpreter"
            )
        self.task_id = task_id
        self.work_dir = work_dir
        self.work_dir.mkdir(parents=True, exist_ok=True)

        self._sbx: Any = None
        self._image_seq = 0
        self._interrupt_requested = False
        self._notebook: NotebookRecorder = NotebookRecorder()
        self._notebook_path: Path = self.work_dir / "notebook.ipynb"

    # ── 上下文管理器 ──────────────────────────────────────────────────────────

    async def __aenter__(self) -> "E2BSandbox":
        await self.start()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.stop()

    # ── 生命周期 ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        settings = get_settings()
        api_key = settings.e2b_api_key
        if not api_key:
            raise RuntimeError("E2B_API_KEY 未配置，无法使用云端沙箱")

        logger.info("E2B sandbox starting | task={}", self.task_id)
        self._sbx = await _E2BSandbox.create(api_key=api_key, timeout=3600)

        # 上传工作区现有数据文件
        await self._upload_data_files()

        # 初始化 matplotlib 环境（与本地内核保持一致）
        init_code = (
            "import warnings, os\n"
            "warnings.filterwarnings('ignore')\n"
            "import matplotlib\n"
            "matplotlib.use('Agg')\n"
            "import matplotlib.pyplot as plt\n"
            "import numpy as np, pandas as pd\n"
        )
        await self._sbx.run_code(init_code)
        logger.info("E2B sandbox ready | task={}", self.task_id)

    async def stop(self) -> None:
        if self._sbx:
            try:
                await self._sbx.kill()
            except Exception as e:
                logger.warning("E2B sandbox kill failed: {} | task={}", e, self.task_id)
            self._sbx = None

    async def interrupt(self) -> None:
        """向 E2B 发送中断信号（尽力而为）。"""
        self._interrupt_requested = True
        if self._sbx:
            try:
                await self._sbx.interrupt()
                logger.info("E2B interrupt sent | task={}", self.task_id)
            except Exception as e:
                logger.warning("E2B interrupt failed: {} | task={}", e, self.task_id)

    # ── 辅助方法 ──────────────────────────────────────────────────────────────

    async def _upload_data_files(self) -> None:
        """将工作区中的数据文件上传到 E2B 容器。"""
        exts = {".csv", ".xlsx", ".xls", ".json", ".txt", ".tsv"}
        for p in self.work_dir.iterdir():
            if p.is_file() and p.suffix.lower() in exts:
                try:
                    content = p.read_bytes()
                    await self._sbx.filesystem.write_bytes(f"/home/user/{p.name}", content)
                    logger.debug("E2B uploaded: {} | task={}", p.name, self.task_id)
                except Exception as e:
                    logger.warning("E2B upload {} failed: {} | task={}", p.name, e, self.task_id)

    def _save_image(self, b64: str, ext: str) -> Path:
        """将 base64 图片解码并保存到工作区，返回路径。"""
        self._image_seq += 1
        path = self.work_dir / f"figure_{self._image_seq:03d}.{ext}"
        path.write_bytes(base64.b64decode(b64))
        return path

    def add_phase_marker(self, label: str) -> None:
        """插入分节标题 cell 到 notebook。"""
        self._notebook.add_phase_marker(label)
        self._notebook.flush(self._notebook_path)

    async def _check_paused(self) -> None:
        """暂停感知：与 JupyterSandbox 保持一致。"""
        try:
            from ..tasks.manager import task_manager
            await task_manager.wait_if_paused(self.task_id)
        except Exception:
            pass

    # ── 核心执行 ──────────────────────────────────────────────────────────────

    async def execute(
        self, code: str, *, timeout: Optional[int] = None, emit_events: bool = True
    ) -> ExecResult:
        """在 E2B 远程容器执行代码，返回 ExecResult（与本地沙箱兼容）。"""
        await self._check_paused()

        if self._sbx is None:
            return ExecResult(success=False, error="E2B sandbox not started")

        settings = get_settings()
        _timeout = timeout or settings.sandbox_timeout

        result = ExecResult(success=True)
        self._notebook.record_cell(code)

        try:
            execution = await asyncio.wait_for(
                self._sbx.run_code(code),
                timeout=_timeout,
            )
        except asyncio.TimeoutError:
            result.success = False
            result.error = f"E2B execution timeout after {_timeout}s"
            self._notebook.record_error("TimeoutError", result.error, [])
            self._notebook.flush(self._notebook_path)
            return result
        except Exception as e:
            result.success = False
            result.error = str(e)
            self._notebook.record_error("ExecutionError", str(e), [])
            self._notebook.flush(self._notebook_path)
            return result

        # ── 处理 stdout / stderr ──────────────────────────────────────────────
        if execution.logs.stdout:
            text = "\n".join(execution.logs.stdout)
            result.stdout = text
            self._notebook.record_stdout(text)
            if emit_events:
                await emit(EventType.SANDBOX_STDOUT, self.task_id, text=text)

        if execution.logs.stderr:
            text = "\n".join(execution.logs.stderr)
            result.stderr = text
            self._notebook.record_stderr(text)
            if emit_events:
                await emit(EventType.SANDBOX_STDERR, self.task_id, text=text)

        # ── 处理 error ────────────────────────────────────────────────────────
        if execution.error:
            result.success = False
            result.error = f"{execution.error.name}: {execution.error.value}"
            tb = execution.error.traceback or []
            result.traceback = tb if isinstance(tb, list) else [tb]
            self._notebook.record_error(
                execution.error.name,
                execution.error.value,
                result.traceback,
            )
            if emit_events:
                await emit(
                    EventType.SANDBOX_STDERR,
                    self.task_id,
                    text=result.error + "\n" + "\n".join(result.traceback),
                )

        # ── 处理 results（图片 / 文本结果）──────────────────────────────────
        for res in (execution.results or []):
            if hasattr(res, "png") and res.png:
                img_path = self._save_image(res.png, "png")
                result.images.append(str(img_path))
                self._notebook.record_image(res.png, "image/png")
                if emit_events:
                    await emit(
                        EventType.SANDBOX_DISPLAY, self.task_id,
                        image=str(img_path), kind="png",
                    )
            elif hasattr(res, "jpeg") and res.jpeg:
                img_path = self._save_image(res.jpeg, "jpg")
                result.images.append(str(img_path))
                self._notebook.record_image(res.jpeg, "image/jpeg")
                if emit_events:
                    await emit(
                        EventType.SANDBOX_DISPLAY, self.task_id,
                        image=str(img_path), kind="jpg",
                    )
            if hasattr(res, "text") and res.text and not getattr(res, "png", None):
                result.text_result = res.text
                self._notebook.record_result(res.text)
                if emit_events:
                    await emit(EventType.SANDBOX_RESULT, self.task_id, text=res.text)

        # ── 下载新生成的图片文件（plt.savefig 直接存文件的情况）────────────
        await self._sync_output_files()

        self._notebook.flush(self._notebook_path)

        if self._interrupt_requested:
            self._interrupt_requested = False
            if not result.error:
                result.success = False
                result.error = "KeyboardInterrupt: 任务被用户中断"

        return result

    async def _sync_output_files(self) -> None:
        """同步 E2B 容器中新生成的图片/文件到本地工作区。"""
        try:
            remote_files = await self._sbx.filesystem.list("/home/user/")
            for f in remote_files:
                name = f.name if hasattr(f, "name") else str(f)
                local_path = self.work_dir / name
                if local_path.exists():
                    continue   # 已存在（上传的原始数据文件）
                ext = Path(name).suffix.lower()
                if ext in {".png", ".jpg", ".jpeg", ".svg", ".md"}:
                    try:
                        content = await self._sbx.filesystem.read_bytes(f"/home/user/{name}")
                        local_path.write_bytes(content)
                        logger.debug("E2B synced: {} | task={}", name, self.task_id)
                    except Exception as e:
                        logger.warning("E2B sync {} failed: {} | task={}", name, e, self.task_id)
        except Exception as e:
            logger.warning("E2B file sync failed: {} | task={}", e, self.task_id)
