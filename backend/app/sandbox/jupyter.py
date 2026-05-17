"""本地 Jupyter Kernel 沙箱。

- 每个任务持有独立 KernelManager，状态隔离
- 工作目录指向任务工作区，所有产物落地磁盘
- 执行结果按 stream / display / execute_result / error 归类
- 执行同时通过事件总线推送，支撑前端实时显示
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import re as _re

from jupyter_client.manager import AsyncKernelManager

from ..core.config import get_settings
from ..core.events import EventType, emit
from ..core.logging import logger
from .notebook import NotebookRecorder
from .preamble import build_init_code
from .preamble.loader import parse_cjk_marker

# ---------- CJK 字体缓存（hint，仅作提示，kernel 内仍验证）----------
_FONT_CACHE_TTL = 86400.0   # 24 小时
_FONT_CACHE_PATH = Path.home() / ".cache" / "mathoi" / "cjk_font_cache.json"


def _load_cjk_cache_hint() -> dict:
    """读取本地缓存，返回 {font, path}（空值表示未知）。"""
    try:
        if _FONT_CACHE_PATH.exists():
            data = json.loads(_FONT_CACHE_PATH.read_text(encoding="utf-8"))
            if time.time() - float(data.get("ts", 0)) < _FONT_CACHE_TTL:
                return {
                    "font": str(data.get("font") or ""),
                    "path": str(data.get("path") or ""),
                }
    except Exception:
        pass
    return {"font": "", "path": ""}


def _save_cjk_cache(font_name: Optional[str], font_path: Optional[str] = None) -> None:
    """将探测结果回写缓存文件（font="" 也缓存，避免下次再扫）。"""
    try:
        _FONT_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _FONT_CACHE_PATH.write_text(
            json.dumps({
                "font": font_name or "",
                "path": font_path or "",
                "ts": time.time(),
            }),
            encoding="utf-8",
        )
    except Exception:
        pass


# 清理 ANSI 转义码（traceback 中 colorama 输出会干扰 LLM 理解）
_ANSI_RE = _re.compile(r"\x1b\[[0-9;]*[mGKHF]")
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
_KERNEL_HEALTH_INTERVAL = 60.0  # 后台健康检查间隔（秒）
_KERNEL_HEALTH_TIMEOUT = 5.0    # is_alive 单次超时（秒）
_KERNEL_HEALTH_MAX_FAILS = 2    # 连续失败多少次后重启


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
        self._interrupt_requested: bool = False  # 用户发起的中断标志
        self._notebook: NotebookRecorder = NotebookRecorder()
        self._notebook_path: Path = self.work_dir / "notebook.ipynb"
        self._health_task: Optional[asyncio.Task] = None

    async def _check_paused(self) -> None:
        """若任务被暂停则阻塞，直到恢复或取消。不中断内核，仅暂停消息处理。"""
        try:
            from ..tasks.manager import task_manager  # 懒导入，避免循环依赖
            await task_manager.wait_if_paused(self.task_id)
        except Exception:
            pass

    def add_phase_marker(self, label: str) -> None:
        """在 notebook 中插入分节标题 cell（由 orchestrator 在每个阶段开始时调用）。"""
        self._notebook.add_phase_marker(label)
        self._notebook.flush(self._notebook_path)

    async def interrupt(self) -> None:
        """中断当前正在执行的代码（等价于 Jupyter 的 '■ Stop' 按钮）。"""
        self._interrupt_requested = True
        if self._km:
            try:
                await self._km.interrupt_kernel()
                logger.info("Kernel interrupt sent | task={}", self.task_id)
            except Exception as e:
                logger.warning("Kernel interrupt failed: {} | task={}", e, self.task_id)

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
                # 预热：注入工作目录 + CJK 探测 + matplotlib 学术风格
                _hint = _load_cjk_cache_hint()
                _init_code = build_init_code(
                    self.work_dir,
                    hint_font=_hint["font"],
                    hint_path=_hint["path"],
                )
                _init_result = await self._silent_exec(_init_code)
                # 解析 sandbox 打印的标记（__MATHOI_CJK__:<font>|<path>），回写主进程缓存
                _f, _p = parse_cjk_marker(_init_result)
                if _f or _p:
                    _save_cjk_cache(_f or None, _p or None)
                logger.info("Sandbox started (attempt {}) | task={} cwd={}",
                            attempt, self.task_id, self.work_dir)
                self._start_health_worker()
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
        current = asyncio.current_task()
        # 若由健康检查协程自身触发重启，不能 cancel/await 自己；否则会自等待死锁。
        if self._health_task and not self._health_task.done() and self._health_task is not current:
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            self._health_task = None
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

    def _start_health_worker(self) -> None:
        """启动后台 Kernel 健康检查。"""
        if self._health_task is None or self._health_task.done():
            self._health_task = asyncio.create_task(self._health_worker())

    async def _health_worker(self) -> None:
        """定期检测 Kernel 存活；空闲时连续失败自动重启。"""
        fails = 0
        while True:
            try:
                await asyncio.sleep(_KERNEL_HEALTH_INTERVAL)
                await self._check_paused()
                # 正在执行代码时跳过健康检查，避免与 execute 的 ZMQ 消息读取互相干扰
                if self._lock.locked():
                    continue
                km = self._km
                if km is None:
                    continue
                alive = await asyncio.wait_for(km.is_alive(), timeout=_KERNEL_HEALTH_TIMEOUT)
                if alive:
                    fails = 0
                    continue
                fails += 1
                logger.warning("Kernel health check failed {}/{} | task={}",
                               fails, _KERNEL_HEALTH_MAX_FAILS, self.task_id)
                if fails >= _KERNEL_HEALTH_MAX_FAILS:
                    async with self._lock:
                        logger.warning("Kernel unhealthy, restarting | task={}", self.task_id)
                        await self._restart_kernel()
                    fails = 0
            except asyncio.CancelledError:
                return
            except Exception as e:
                fails += 1
                logger.warning("Kernel health check error {}/{}: {} | task={}",
                               fails, _KERNEL_HEALTH_MAX_FAILS, e, self.task_id)
                if fails >= _KERNEL_HEALTH_MAX_FAILS and not self._lock.locked():
                    try:
                        async with self._lock:
                            await self._restart_kernel()
                        fails = 0
                    except Exception as re:
                        logger.warning("Kernel health restart failed: {} | task={}", re, self.task_id)

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

    async def _silent_exec(self, code: str) -> str:
        """内部预热执行，不发事件。预热失败只记 warning，不中断启动。
        返回执行期间的 stdout 输出（供调用方解析特殊标记）。
        """
        if self._kc is None:
            return ""
        stdout_buf: list[str] = []
        try:
            msg_id = self._kc.execute(code)
            deadline = asyncio.get_running_loop().time() + 30
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    logger.warning("sandbox warmup timeout | task={}", self.task_id)
                    break
                try:
                    msg = await asyncio.wait_for(self._kc.get_iopub_msg(), timeout=min(remaining, 10))
                except asyncio.TimeoutError:
                    break
                msg_type = msg.get("msg_type", "")
                parent_id = msg.get("parent_header", {}).get("msg_id")
                if parent_id == msg_id:
                    if msg_type == "stream":
                        stdout_buf.append(msg["content"].get("text", ""))
                    elif msg_type == "status" and msg["content"].get("execution_state") == "idle":
                        break
        except Exception as e:
            logger.warning("sandbox warmup error (non-fatal): {} | task={}", e, self.task_id)
        return "".join(stdout_buf)

    async def execute(self, code: str, *, timeout: Optional[int] = None, emit_events: bool = True) -> ExecResult:
        """执行一段代码，返回结构化结果。执行前检测内核存活，崩溃则自动重启。"""
        # 执行前检查：若任务已被暂停，等待恢复后再发起新 cell
        await self._check_paused()
        async with self._lock:
            # 检测内核是否还活着，死亡则重启
            if self._km is not None:
                try:
                    # 加 5s 超时：ZMQ socket 活性检查有时卡死
                    alive = await asyncio.wait_for(self._km.is_alive(), timeout=5.0)
                    if not alive:
                        logger.warning("Kernel not alive before execute, restarting | task={}", self.task_id)
                        await self._restart_kernel()
                except (asyncio.TimeoutError, Exception) as e:
                    logger.warning("Kernel liveness check failed ({}), restarting | task={}", e, self.task_id)
                    await self._restart_kernel()
            return await self._execute_impl(code, timeout=timeout, emit_events=emit_events)

    async def _execute_impl(self, code: str, *, timeout: Optional[int], emit_events: bool) -> ExecResult:
        assert self._kc is not None
        settings = get_settings()
        timeout = timeout or settings.sandbox_timeout

        result = ExecResult(success=True)
        # ── Notebook 录制：记录代码 cell（在发送给 kernel 之前）────────────
        self._notebook.record_cell(code)
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
            # 暂停检查：每轮循环前检查，确保 pause 能在 1s 内生效
            await self._check_paused()
            try:
                # 封顶 1s，保证暂停请求能在下一轮被及时响应
                msg = await asyncio.wait_for(
                    self._kc.get_iopub_msg(),
                    timeout=min(remaining, 1.0),
                )
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
                    self._notebook.record_stderr(text)
                    if emit_events:
                        await emit(EventType.SANDBOX_STDERR, self.task_id, text=text)
                else:
                    result.stdout += text
                    self._notebook.record_stdout(text)
                    if emit_events:
                        await emit(EventType.SANDBOX_STDOUT, self.task_id, text=text)

            elif mtype in ("display_data", "execute_result"):
                data = content.get("data", {})
                if "image/png" in data:
                    img_path = self._save_image(data["image/png"], "png")
                    result.images.append(str(img_path))
                    self._notebook.record_image(data["image/png"], "image/png")
                    if emit_events:
                        await emit(EventType.SANDBOX_DISPLAY, self.task_id, image=str(img_path), kind="png")
                elif "image/jpeg" in data:
                    img_path = self._save_image(data["image/jpeg"], "jpg")
                    result.images.append(str(img_path))
                    self._notebook.record_image(data["image/jpeg"], "image/jpeg")
                    if emit_events:
                        await emit(EventType.SANDBOX_DISPLAY, self.task_id, image=str(img_path), kind="jpg")
                if "text/plain" in data and mtype == "execute_result":
                    result.text_result = data["text/plain"]
                    self._notebook.record_result(data["text/plain"])
                    if emit_events:
                        await emit(EventType.SANDBOX_RESULT, self.task_id, text=result.text_result)

            elif mtype == "error":
                result.success = False
                raw_err = content.get("ename", "") + ": " + content.get("evalue", "")
                raw_tb = content.get("traceback", [])
                # 清理 ANSI 转义码，避免 LLM 收到乱码
                result.error = _ANSI_RE.sub("", raw_err)
                result.traceback = [_ANSI_RE.sub("", line) for line in raw_tb]
                self._notebook.record_error(
                    content.get("ename", "Error"),
                    content.get("evalue", ""),
                    raw_tb,
                )
                if emit_events:
                    await emit(
                        EventType.SANDBOX_STDERR,
                        self.task_id,
                        text=result.error + "\n" + "\n".join(result.traceback),
                    )

            elif mtype == "status" and content.get("execution_state") == "idle":
                # cell 执行完毕：刷新 notebook（增量覆盖，保持文件始终可用）
                self._notebook.flush(self._notebook_path)
                break

        # 清除中断标志
        if self._interrupt_requested:
            self._interrupt_requested = False
            if not result.error:
                result.success = False
                result.error = "KeyboardInterrupt: 任务被用户中断"
        return result

    def _save_image(self, b64: str, ext: str) -> Path:
        self._image_seq += 1
        path = self.work_dir / f"figure_{self._image_seq:03d}.{ext}"
        path.write_bytes(base64.b64decode(b64))
        return path
