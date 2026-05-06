"""本地 Jupyter Kernel 沙箱。

- 每个任务持有独立 KernelManager，状态隔离
- 工作目录指向任务工作区，所有产物落地磁盘
- 执行结果按 stream / display / execute_result / error 归类
- 执行同时通过事件总线推送，支撑前端实时显示
"""
from __future__ import annotations

import asyncio
import base64
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from jupyter_client.manager import AsyncKernelManager

from ..core.config import get_settings
from ..core.events import EventType, emit
from ..core.logging import logger


@dataclass
class ExecResult:
    success: bool
    stdout: str = ""
    stderr: str = ""
    text_result: str = ""               # text/plain 形式的最终结果
    images: list[str] = field(default_factory=list)  # 本地保存路径
    error: Optional[str] = None
    traceback: list[str] = field(default_factory=list)

    def to_llm_feedback(self, max_chars: int = 4000) -> str:
        """压缩成给 LLM 的反馈文本。"""
        parts: list[str] = []
        if self.stdout:
            parts.append(f"[stdout]\n{self.stdout}")
        if self.text_result:
            parts.append(f"[result]\n{self.text_result}")
        if self.images:
            parts.append(f"[images saved]\n" + "\n".join(self.images))
        if self.stderr:
            parts.append(f"[stderr]\n{self.stderr}")
        if self.error:
            parts.append(f"[error]\n{self.error}\n" + "\n".join(self.traceback[-10:]))
        text = "\n\n".join(parts) if parts else "[no output]"
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n... (truncated, total {len(text)} chars)"
        return text


_KERNEL_START_TIMEOUT = 90   # wait_for_ready 超时（秒）
_KERNEL_START_RETRIES = 3    # 最大重试次数
_KERNEL_RETRY_DELAY  = 3     # 重试间隔（秒）


class JupyterSandbox:
    """异步本地 Kernel 沙箱。"""

    def __init__(self, task_id: str, work_dir: Path) -> None:
        self.task_id = task_id
        self.work_dir = work_dir
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self._km: Optional[AsyncKernelManager] = None
        self._kc: Any = None
        self._image_seq = 0
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        settings = get_settings()
        last_exc: Exception = RuntimeError("unknown")
        for attempt in range(1, _KERNEL_START_RETRIES + 1):
            try:
                # 每次尝试前彻底清理上一次的残留
                await self._cleanup()
                self._km = AsyncKernelManager(kernel_name=settings.sandbox_kernel)
                await self._km.start_kernel(cwd=str(self.work_dir))
                self._kc = self._km.client()
                self._kc.start_channels()
                await self._kc.wait_for_ready(timeout=_KERNEL_START_TIMEOUT)
                # 预热：注入工作目录 + 学术 matplotlib 全局配置
                await self._silent_exec(
                    "import os, sys, json, gc\n"
                    "import matplotlib\n"
                    "matplotlib.use('Agg')\n"
                    "import matplotlib.pyplot as plt\n"
                    "import matplotlib.font_manager as _fm\n"
                    "import warnings\n"
                    "warnings.filterwarnings('ignore')\n"
                    f"os.chdir(r'{self.work_dir}')\n"
                    "\n"
                    "# ── 学术论文可视化全局配置 ────────────────────────────\n"
                    "plt.rcParams.update({\n"
                    "    'font.size': 11,\n"
                    "    'axes.titlesize': 12,\n"
                    "    'axes.titleweight': 'bold',\n"
                    "    'axes.labelsize': 11,\n"
                    "    'axes.linewidth': 1.2,\n"
                    "    'axes.spines.top': False,\n"
                    "    'axes.spines.right': False,\n"
                    "    'xtick.labelsize': 10,\n"
                    "    'ytick.labelsize': 10,\n"
                    "    'legend.fontsize': 10,\n"
                    "    'legend.frameon': False,\n"
                    "    'figure.dpi': 150,\n"
                    "    'savefig.dpi': 300,\n"
                    "    'savefig.bbox': 'tight',\n"
                    "    'savefig.pad_inches': 0.1,\n"
                    "})\n"
                    "plt.rcParams['font.sans-serif'] = "
                    "['Noto Sans CJK JP', 'SimHei', 'Microsoft YaHei', 'WenQuanYi Micro Hei', "
                    "'Heiti SC', 'DejaVu Sans']\n"
                    "plt.rcParams['axes.unicode_minus'] = False\n"
                    "\n"
                    "# ── 标准配色与图幅常量（Coder 可直接使用）────────────\n"
                    "COLORS = {\n"
                    "    'primary':   '#2E5B88',\n"
                    "    'secondary': '#E85D4C',\n"
                    "    'tertiary':  '#4A9B7F',\n"
                    "    'neutral':   '#7F7F7F',\n"
                    "    'light':     '#B8D4E8',\n"
                    "}\n"
                    "FIG_SINGLE = (5, 4)\n"
                    "FIG_DOUBLE = (10, 4)\n"
                    "FIG_WIDE   = (8, 3)\n"
                    "FIG_SQUARE = (6, 6)\n"
                    "print('Sandbox ready | cwd:', os.getcwd())\n"
                )
                logger.info("Sandbox started (attempt {}) | task={} cwd={}",
                            attempt, self.task_id, self.work_dir)
                return  # 成功
            except Exception as e:
                last_exc = e
                logger.warning("Sandbox start attempt {}/{} failed: {}", attempt, _KERNEL_START_RETRIES, e)
                await self._cleanup()
                if attempt < _KERNEL_START_RETRIES:
                    await asyncio.sleep(_KERNEL_RETRY_DELAY)
        raise RuntimeError(
            f"Kernel 启动失败（已重试 {_KERNEL_START_RETRIES} 次）：{last_exc}\n"
            "请检查 ipykernel 是否已安装（pip install ipykernel）"
        ) from last_exc

    async def _cleanup(self) -> None:
        """彻底关闭当前 km/kc，忽略所有异常。"""
        # 先摘引用，再操作，避免重入
        kc, km = self._kc, self._km
        self._kc = None
        self._km = None
        try:
            if kc:
                kc.stop_channels()
        except Exception:
            pass
        try:
            if km:
                await km.shutdown_kernel(now=True)
        except Exception:
            pass

    async def stop(self) -> None:
        await self._cleanup()

    async def _restart_kernel(self) -> None:
        """执行中发现内核崩溃时，原地重启。"""
        logger.warning("Kernel dead, restarting | task={}", self.task_id)
        await self._cleanup()
        await self.start()

    async def __aenter__(self) -> "JupyterSandbox":
        await self.start()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.stop()

    async def _silent_exec(self, code: str) -> None:
        """内部预热执行，不发事件。"""
        assert self._kc is not None
        msg_id = self._kc.execute(code)
        while True:
            try:
                msg = await asyncio.wait_for(self._kc.get_iopub_msg(), timeout=10)
            except asyncio.TimeoutError:
                break
            if (
                msg.get("parent_header", {}).get("msg_id") == msg_id
                and msg.get("msg_type") == "status"
                and msg["content"].get("execution_state") == "idle"
            ):
                break

    async def execute(self, code: str, *, timeout: Optional[int] = None, emit_events: bool = True) -> ExecResult:
        """执行一段代码，返回结构化结果。执行前检测内核存活，崩溃则自动重启。"""
        async with self._lock:
            # 检测内核是否还活着，死亡则重启
            if self._km is not None:
                try:
                    alive = await self._km.is_alive()
                    if not alive:
                        logger.warning("Kernel not alive before execute, restarting | task={}", self.task_id)
                        await self._restart_kernel()
                except Exception as e:
                    logger.warning("Kernel liveness check failed ({}), restarting | task={}", e, self.task_id)
                    await self._restart_kernel()
            return await self._execute_impl(code, timeout=timeout, emit_events=emit_events)

    async def _execute_impl(self, code: str, *, timeout: Optional[int], emit_events: bool) -> ExecResult:
        assert self._kc is not None
        settings = get_settings()
        timeout = timeout or settings.sandbox_timeout

        result = ExecResult(success=True)
        msg_id = self._kc.execute(code)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout

        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                result.success = False
                result.error = f"Execution timeout after {timeout}s"
                try:
                    if self._km:
                        await self._km.interrupt_kernel()
                except Exception:
                    pass
                break
            try:
                msg = await asyncio.wait_for(self._kc.get_iopub_msg(), timeout=remaining)
            except asyncio.TimeoutError:
                continue
            if msg.get("parent_header", {}).get("msg_id") != msg_id:
                continue

            mtype = msg["msg_type"]
            content = msg["content"]

            if mtype == "stream":
                text = content.get("text", "")
                stream_name = content.get("name", "stdout")
                if stream_name == "stderr":
                    result.stderr += text
                    if emit_events:
                        await emit(EventType.SANDBOX_STDERR, self.task_id, text=text)
                else:
                    result.stdout += text
                    if emit_events:
                        await emit(EventType.SANDBOX_STDOUT, self.task_id, text=text)

            elif mtype in ("display_data", "execute_result"):
                data = content.get("data", {})
                if "image/png" in data:
                    img_path = self._save_image(data["image/png"], "png")
                    result.images.append(str(img_path))
                    if emit_events:
                        await emit(EventType.SANDBOX_DISPLAY, self.task_id, image=str(img_path), kind="png")
                elif "image/jpeg" in data:
                    img_path = self._save_image(data["image/jpeg"], "jpg")
                    result.images.append(str(img_path))
                    if emit_events:
                        await emit(EventType.SANDBOX_DISPLAY, self.task_id, image=str(img_path), kind="jpg")
                if "text/plain" in data and mtype == "execute_result":
                    result.text_result = data["text/plain"]
                    if emit_events:
                        await emit(EventType.SANDBOX_RESULT, self.task_id, text=result.text_result)

            elif mtype == "error":
                result.success = False
                result.error = content.get("ename", "") + ": " + content.get("evalue", "")
                result.traceback = content.get("traceback", [])
                if emit_events:
                    await emit(
                        EventType.SANDBOX_STDERR,
                        self.task_id,
                        text=result.error + "\n" + "\n".join(result.traceback),
                    )

            elif mtype == "status" and content.get("execution_state") == "idle":
                break

        return result

    def _save_image(self, b64: str, ext: str) -> Path:
        self._image_seq += 1
        path = self.work_dir / f"figure_{self._image_seq:03d}.{ext}"
        path.write_bytes(base64.b64decode(b64))
        return path
