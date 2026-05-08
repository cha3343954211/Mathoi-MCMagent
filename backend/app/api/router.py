"""HTTP + WebSocket 路由（多用户 + 模型个性化）。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import get_current_user, get_user_from_token
from ..core.events import bus
from ..core.logging import logger
from ..db import AsyncSessionLocal, User, get_session
from ..services.model_service import (
    DEFAULT_AGENTS, _fetch_preset, list_configs, list_presets, resolve_effective,
    select_user_preset, upsert_config,
)
from ..core.crypto import decrypt
from ..services.usage_service import stats_for_task
from ..tasks import TaskState, task_manager
from ..core.config import get_settings
from ..workflow import run_workflow
from .schemas import TaskResponse


async def _run_workflow_with_timeout(task_id: str) -> None:
    """run_workflow 超时包装：超出 max_task_hours 则将任务标为 FAILED。"""
    from ..core.events import EventType, emit as emit_event
    settings = get_settings()
    timeout_secs = settings.max_task_hours * 3600
    try:
        await asyncio.wait_for(run_workflow(task_id), timeout=timeout_secs)
    except asyncio.TimeoutError:
        logger.error("workflow timeout after {:.1f}h | task={}", settings.max_task_hours, task_id)
        await task_manager.update_state(
            task_id, TaskState.FAILED,
            error=f"任务超时（>{settings.max_task_hours}小时）",
        )
        await emit_event(EventType.TASK_FAILED, task_id, error="任务超时")

router = APIRouter()


def _to_response(t) -> TaskResponse:
    return TaskResponse(
        task_id=t.task_id, user_id=t.user_id,
        title=t.title, problem=t.problem,
        state=t.state.value if hasattr(t.state, "value") else str(t.state),
        phase=t.phase, error=t.error, work_dir=t.work_dir,
        data_files=t.data_files, created_at=t.created_at, updated_at=t.updated_at,
        hitl_request=t.hitl_request,
    )


def _ensure_owner_or_admin(t, user: User) -> None:
    if t is None:
        raise HTTPException(404, "task not found")
    if t.user_id != user.id and user.role != "admin":
        raise HTTPException(403, "无权访问该任务")


# ---------- 健康 ----------
@router.get("/health")
async def health() -> dict[str, Any]:
    import time as _t
    from sqlalchemy import text as _text
    db_ok = True
    try:
        async with AsyncSessionLocal() as _s:
            await _s.execute(_text("SELECT 1"))
    except Exception:
        db_ok = False
    tasks = task_manager._tasks
    by_state: dict[str, int] = {}
    for _t2 in tasks.values():
        by_state[_t2.state.value] = by_state.get(_t2.state.value, 0) + 1
    return {
        "ok": True,
        "service": "mathoi-agent",
        "db": "ok" if db_ok else "error",
        "tasks_in_memory": len(tasks),
        "by_state": by_state,
        "active_kernels": len(task_manager._sandboxes),
        "ts": _t.time(),
    }


# ---------- 模型配置（当前用户视角） ----------
# ---------- API Key 校验 ----------
class ValidateKeyRequest(BaseModel):
    model: str
    api_key: str
    base_url: Optional[str] = None
    backend: str = "openai"   # 'openai'（含兼容接口）或 'litellm'


async def _do_model_test(
    model: str,
    api_key: str,
    base_url: Optional[str],
    backend: str = "openai",
) -> dict[str, Any]:
    """连通性测试：与 llm/client.py 推理路径保持一致。

    - backend='openai'（默认）：使用 AsyncOpenAI 客户端，支持自定义 base_url
    - backend='litellm'：使用 litellm.acompletion，适用于 Claude/Gemini/Azure 等
    """
    try:
        if backend == "litellm":
            import litellm
            params: dict[str, Any] = {
                "model": model,
                "messages": [{"role": "user", "content": "Hi"}],
                "max_tokens": 1,
            }
            if base_url:
                params["api_base"] = base_url
            if api_key:
                params["api_key"] = api_key
            await litellm.acompletion(**params)
        else:
            # openai backend：直接用 AsyncOpenAI，支持 DeepSeek / 硅基 / 任何 OpenAI 兼容接口
            from openai import AsyncOpenAI
            client = AsyncOpenAI(
                api_key=api_key or "placeholder",
                base_url=base_url or None,
                timeout=30.0,
            )
            await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Hi"}],
                max_tokens=1,
            )
        return {"valid": True, "message": "✓ 连接成功"}
    except Exception as e:
        err = str(e)
        if "401" in err or "Unauthorized" in err or "invalid" in err.lower():
            msg = "✗ API Key 无效或已过期"
        elif "404" in err or "not found" in err.lower():
            msg = "✗ 模型 ID 不存在或 Base URL 错误"
        elif "429" in err or "rate" in err.lower():
            msg = "✗ 请求过于频繁，请稍后再试"
        elif "403" in err or "Forbidden" in err:
            msg = "✗ 权限不足或余额不足"
        elif "connect" in err.lower() or "timeout" in err.lower() or "network" in err.lower():
            msg = "✗ 无法连接到接口，请检查 Base URL"
        else:
            msg = f"✗ 验证失败: {err[:120]}"
        return {"valid": False, "message": msg}


@router.post("/models/validate")
async def validate_api_key(
    body: ValidateKeyRequest,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """发送一条测试请求，验证 API Key / Base URL / 模型 ID 是否可用。"""
    return await _do_model_test(body.model, body.api_key, body.base_url, body.backend)


@router.post("/models/presets/{preset_id}/test")
async def test_preset_connectivity(
    preset_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """使用预设服务端存储的密钥测试模型连通性（无需前端传入 API Key）。"""
    p = await _fetch_preset(session, preset_id)
    if p is None:
        raise HTTPException(404, "预设不存在或已停用")
    api_key = decrypt(p.api_key_enc) if p.api_key_enc else ""
    backend = p.backend or "openai"
    return await _do_model_test(p.model, api_key, p.base_url or None, backend)


class UserModelUpdate(BaseModel):
    agent: str = Field(..., pattern=r"^(default|coordinator|modeler|coder|writer)$")
    backend: Optional[str] = Field(None, pattern=r"^(openai|litellm)$")
    model: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None  # "" 不改动；"__clear__" 清空
    temperature: Optional[float] = Field(None, ge=0, le=2)
    max_tokens: Optional[int] = Field(None, ge=0)


class ToggleDefault(BaseModel):
    use_default_model: bool


@router.get("/models")
async def get_my_models(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    defaults = await list_configs(session, owner_id=None)
    mine = await list_configs(session, owner_id=user.id)
    effective = {}
    for a in DEFAULT_AGENTS:
        r = await resolve_effective(session, user=user, agent=a)
        effective[a] = {
            "agent": a, "backend": r.backend, "model": r.model,
            "base_url": r.base_url, "has_api_key": bool(r.api_key),
            "temperature": r.temperature, "is_default": r.is_default,
        }
    return {
        "use_default_model": user.use_default_model,
        "agents": DEFAULT_AGENTS,
        "defaults": defaults,   # 管理员维护（普通用户只读视图，不含明文 key）
        "mine": mine,            # 用户自定义
        "effective": effective,  # 最终生效
    }


@router.get("/models/list")
async def list_provider_models(
    base_url: str,
    agent: str = "default",
    api_key: str = "",
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[str]:
    """向 OpenAI 兼容接口拉取可用模型列表（前端下拉用）。
    api_key 为空时，自动从数据库读取该 agent 的已存密钥。
    """
    import httpx
    from ..services.model_service import resolve_effective

    effective_key = api_key
    if not effective_key:
        cfg = await resolve_effective(session, user=user, agent=agent)
        effective_key = cfg.api_key or ""

    url = base_url.rstrip("/") + "/models"
    headers: dict[str, str] = {}
    if effective_key:
        headers["Authorization"] = f"Bearer {effective_key}"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            ids = [m["id"] for m in data.get("data", []) if isinstance(m, dict) and "id" in m]
            return sorted(ids)
    except httpx.HTTPStatusError as e:
        raise HTTPException(502, f"接口返回 {e.response.status_code}")
    except Exception as e:
        raise HTTPException(502, f"拉取失败: {e}")


@router.get("/models/presets")
async def get_available_presets(
    agent: str = "all",
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """获取管理员配置的可用预设列表（用户侧，不含明文 key）。
    pro_only 预设仅对 admin / pro 用户可见。
    """
    presets = await list_presets(session, agent=agent if agent != "all" else None, active_only=True)
    is_privileged = user.role in ("admin", "pro")
    return {
        "presets": [
            {
                "id": p.id, "name": p.name, "description": p.description,
                "agent": p.agent, "backend": p.backend, "model": p.model,
                "base_url": p.base_url, "has_api_key": bool(p.api_key_enc),
                "temperature": p.temperature, "max_tokens": p.max_tokens,
                "pro_only": p.pro_only,
            }
            for p in presets
            if not p.pro_only or is_privileged
        ]
    }


class SelectPreset(BaseModel):
    agent: str = Field(..., pattern=r"^(default|coordinator|modeler|coder|writer)$")
    preset_id: Optional[int] = None   # None = 清除选择


@router.post("/models/presets/select")
async def select_preset(
    body: SelectPreset,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """用户为某个 agent 选择（或清除）预设。"""
    await select_user_preset(session, user_id=user.id, agent=body.agent, preset_id=body.preset_id)
    await session.commit()
    return {"ok": True}


@router.post("/models/toggle")
async def toggle_default(
    body: ToggleDefault,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    u = (await session.execute(select(User).where(User.id == user.id))).scalar_one()
    u.use_default_model = body.use_default_model
    await session.commit()
    return {"ok": True, "use_default_model": u.use_default_model}


@router.post("/models/mine")
async def update_my_model(
    body: UserModelUpdate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await upsert_config(
        session, owner_id=user.id,
        agent=body.agent, backend=body.backend, model=body.model,
        base_url=body.base_url, api_key=body.api_key, temperature=body.temperature,
        max_tokens=body.max_tokens,
    )
    await session.commit()
    return {"ok": True}


# ---------- 任务 ----------
_IMAGE_MIMES = {"image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif"}
_IMAGE_EXTS  = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


async def _effective_upload_limits(settings) -> dict:
    """从 DB 读取管理员设置的上传限制，DB 无记录则回退到 env Settings。"""
    try:
        from ..db import AsyncSessionLocal
        from ..db.models import SystemSetting
        async with AsyncSessionLocal() as s:
            rows = (await s.execute(
                select(SystemSetting).where(SystemSetting.key.in_([
                    "max_upload_file_mb", "max_upload_total_mb", "max_upload_files"
                ]))
            )).scalars().all()
        kv = {r.key: r.value for r in rows}

        def _int(key: str, default: int) -> int:
            raw = kv.get(key, "")
            return int(raw) if raw and raw.isdigit() and int(raw) > 0 else default

        return {
            "max_upload_file_mb":  _int("max_upload_file_mb",  settings.max_upload_file_mb),
            "max_upload_total_mb": _int("max_upload_total_mb", settings.max_upload_total_mb),
            "max_upload_files":    _int("max_upload_files",    settings.max_upload_files),
        }
    except Exception:
        # DB 不可用时降级到 env 默认值，不阻塞上传
        return {
            "max_upload_file_mb":  settings.max_upload_file_mb,
            "max_upload_total_mb": settings.max_upload_total_mb,
            "max_upload_files":    settings.max_upload_files,
        }


@router.post("/tasks", response_model=TaskResponse)
async def create_task(
    title: str = Form(...),
    problem: str = Form(...),
    files: list[UploadFile] = File(default_factory=list),
    user: User = Depends(get_current_user),
) -> TaskResponse:
    if not title or len(problem) < 10:
        raise HTTPException(400, "title 与 problem（至少 10 字）必填")

    # 读取有效上传限制：DB 中管理员设置优先，env 兜底
    settings = get_settings()
    _eff = await _effective_upload_limits(settings)
    real_files = [f for f in files if f and f.filename]
    if len(real_files) > _eff["max_upload_files"]:
        raise HTTPException(
            413, f"上传文件数超过上限（{_eff['max_upload_files']}）"
        )
    per_limit = _eff["max_upload_file_mb"] * 1024 * 1024
    total_limit = _eff["max_upload_total_mb"] * 1024 * 1024

    # 先创建任务（获取 work_dir）
    t = await task_manager.create(user_id=user.id, title=title, problem=problem, data_files=[])

    data_saved: list[str] = []
    img_saved:  list[str] = []
    work_dir = Path(t.work_dir)
    saved_paths: list[Path] = []
    total_bytes = 0
    CHUNK = 1024 * 1024   # 1MB 分块

    async def _rollback_partial() -> None:
        """上传失败：删除已落盘文件 + 删任务，避免半成品。"""
        for p in saved_paths:
            try: p.unlink(missing_ok=True)
            except Exception: pass
        try:
            await task_manager.delete(t.task_id)
        except Exception:
            pass

    try:
        for f in real_files:
            safe_name = Path(f.filename).name
            if not safe_name or safe_name in (".", ".."):
                continue
            target = work_dir / safe_name
            written = 0
            with target.open("wb") as out:
                while True:
                    chunk = await f.read(CHUNK)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > per_limit:
                        raise HTTPException(
                            413,
                            f"文件 {safe_name} 超过单文件上限 "
                            f"{settings.max_upload_file_mb}MB",
                        )
                    if total_bytes + written > total_limit:
                        raise HTTPException(
                            413,
                            f"上传总量超过 {settings.max_upload_total_mb}MB",
                        )
                    out.write(chunk)
            total_bytes += written
            saved_paths.append(target)
            is_img = (
                (f.content_type or "").split(";")[0].strip() in _IMAGE_MIMES
                or Path(safe_name).suffix.lower() in _IMAGE_EXTS
            )
            (img_saved if is_img else data_saved).append(safe_name)
    except HTTPException:
        await _rollback_partial()
        raise
    except Exception as e:
        logger.exception("upload failed | task={}", t.task_id)
        await _rollback_partial()
        raise HTTPException(500, f"上传失败: {e}")

    # 同时更新内存对象和持久化
    t.data_files  = data_saved
    t.image_files = img_saved
    await task_manager.update_data_files(t.task_id, data_saved)
    await task_manager.update_image_files(t.task_id, img_saved)

    handle = asyncio.create_task(_run_workflow_with_timeout(t.task_id))
    task_manager.attach_handle(t.task_id, handle)
    return _to_response(t)


@router.get("/tasks", response_model=list[TaskResponse])
async def list_tasks(user: User = Depends(get_current_user)) -> list[TaskResponse]:
    tasks = await task_manager.list_visible_for(user.id, is_admin=(user.role == "admin"))
    # 仅展示自己的任务（即使 admin 也只看自己创建的；管理后台才看全部）
    own = [t for t in tasks if t.user_id == user.id]
    return [_to_response(t) for t in own]


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task(task_id: str, user: User = Depends(get_current_user)) -> TaskResponse:
    t = await task_manager.get_or_load(task_id); _ensure_owner_or_admin(t, user)
    return _to_response(t)


@router.get("/tasks/{task_id}/events")
async def get_history(task_id: str, user: User = Depends(get_current_user)) -> list[dict[str, Any]]:
    t = await task_manager.get_or_load(task_id); _ensure_owner_or_admin(t, user)
    return [e.to_dict() for e in await bus.history_async(task_id)]


@router.post("/tasks/{task_id}/pause")
async def pause(task_id: str, user: User = Depends(get_current_user)) -> dict[str, Any]:
    t = await task_manager.get_or_load(task_id); _ensure_owner_or_admin(t, user)
    await task_manager.pause(task_id)
    return {"ok": True}


@router.post("/tasks/{task_id}/resume")
async def resume(task_id: str, user: User = Depends(get_current_user)) -> dict[str, Any]:
    t = await task_manager.get_or_load(task_id); _ensure_owner_or_admin(t, user)
    await task_manager.resume(task_id)
    return {"ok": True}


@router.post("/tasks/{task_id}/cancel")
async def cancel(task_id: str, user: User = Depends(get_current_user)) -> dict[str, Any]:
    t = await task_manager.get_or_load(task_id); _ensure_owner_or_admin(t, user)
    await task_manager.cancel(task_id)
    return {"ok": True}


@router.post("/tasks/{task_id}/rewrite-section")
async def rewrite_section(
    task_id: str,
    body: dict[str, Any],
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """重写指定 Writer 章节，完成后自动重组 paper.md。

    body: { "section": "abstract" | "restatement" | "analysis" |
                        "assumptions" | "symbol" | "eda" |
                        "q1" | "q2" | ... | "sensitivity" | "evaluation" }
    """
    t = await task_manager.get_or_load(task_id); _ensure_owner_or_admin(t, user)
    if t.state == TaskState.RUNNING:
        raise HTTPException(400, "任务正在运行，请等待完成后再重写章节")

    section_key = (body.get("section") or "").strip().lower()
    if not section_key:
        raise HTTPException(422, "缺少 section 字段")

    from ..workflow.rewrite import run_rewrite_section, SECTION_MAP as _SM
    valid = set(_SM.keys()) | {f"q{i}" for i in range(1, 10)}
    if section_key not in valid:
        raise HTTPException(422, f"不支持的章节 key: {section_key}")

    # 异步后台执行（与主流程一致）
    import asyncio
    handle = asyncio.create_task(
        run_rewrite_section(task_id, section_key, user.id),
        name=f"rewrite-{task_id}-{section_key}",
    )
    task_manager._task_handles[task_id] = handle
    return {"ok": True, "section": section_key}


@router.post("/tasks/{task_id}/interrupt")
async def interrupt_task(task_id: str, user: User = Depends(get_current_user)) -> dict[str, Any]:
    """向当前正在执行代码的 Kernel 发送中断信号，停止死循环。"""
    t = await task_manager.get_or_load(task_id); _ensure_owner_or_admin(t, user)
    ok = await task_manager.interrupt_task(task_id)
    return {"ok": ok, "message": "已发送中断信号" if ok else "无活跃 Kernel，任务可能已完成"}


@router.post("/tasks/{task_id}/retry")
async def retry(task_id: str, user: User = Depends(get_current_user)) -> dict[str, Any]:
    """重试失败/取消的任务：清空错误、重置状态、重新启动工作流。"""
    t = await task_manager.get_or_load(task_id); _ensure_owner_or_admin(t, user)
    if t.state not in (TaskState.FAILED, TaskState.CANCELLED):
        raise HTTPException(400, f"任务当前状态 {t.state.value} 不可重试（仅 failed/cancelled 可重试）")
    # 只取消 asyncio handle，不发 TASK_CANCELLED 事件（避免前端状态闪烁）
    old_handle = task_manager._task_handles.get(task_id)
    if old_handle and not old_handle.done():
        old_handle.cancel()
        # 等旧 handle 真正退出，确保 orchestrator 的 finally 块（sandbox stop / unregister）
        # 跑完，避免新旧两个 sandbox 短暂并存或共享 work_dir 文件锁
        try:
            await asyncio.wait_for(old_handle, timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
            pass
    # 显式释放可能残留的 sandbox（防御性，正常情况下 orchestrator 会自己清）
    task_manager.unregister_sandbox(task_id)
    # 重置 pause_event（避免新工作流在旧 pause 状态下卡住）
    ev = task_manager._pause_events.get(task_id)
    if ev:
        ev.set()
    # 直接重置状态（不走 cancel()，不发 CANCELLED 事件）
    t.error = ""
    t.phase = ""
    await task_manager.update_state(task_id, TaskState.PENDING)
    # 重启工作流
    handle = asyncio.create_task(_run_workflow_with_timeout(task_id))
    task_manager.attach_handle(task_id, handle)
    return {"ok": True}


class HITLReply(BaseModel):
    action: str = "approve"
    edited_plan: Optional[str] = None
    feedback: Optional[str] = None


@router.post("/tasks/{task_id}/hitl")
async def hitl_reply(task_id: str, body: HITLReply, user: User = Depends(get_current_user)) -> dict[str, Any]:
    t = await task_manager.get_or_load(task_id); _ensure_owner_or_admin(t, user)
    await task_manager.reply_hitl(task_id, body.model_dump())
    return {"ok": True}


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str, user: User = Depends(get_current_user)) -> dict:
    t = await task_manager.get_or_load(task_id); _ensure_owner_or_admin(t, user)
    await task_manager.delete(task_id)
    bus.evict(task_id)   # 清除内存缓存（DB 由 CASCADE FK 自动删除）
    return {"ok": True}


# ---------- 任务 token 用量 ----------
@router.get("/tasks/{task_id}/usage")
async def get_task_usage(task_id: str, user: User = Depends(get_current_user)) -> dict[str, Any]:
    t = await task_manager.get_or_load(task_id); _ensure_owner_or_admin(t, user)
    return await stats_for_task(task_id)


# ---------- 文件 ----------
@router.get("/tasks/{task_id}/files")
async def list_files(task_id: str, user: User = Depends(get_current_user)) -> dict[str, Any]:
    t = await task_manager.get_or_load(task_id); _ensure_owner_or_admin(t, user)
    wd = Path(t.work_dir)
    files = []
    for p in sorted(wd.rglob("*")):
        if p.is_file():
            files.append({"name": str(p.relative_to(wd)).replace("\\", "/"), "size": p.stat().st_size})
    return {"work_dir": str(wd), "files": files}


@router.get("/tasks/{task_id}/files/{path:path}")
async def get_file(task_id: str, path: str, user: User = Depends(get_current_user)) -> Any:
    t = await task_manager.get_or_load(task_id); _ensure_owner_or_admin(t, user)
    wd = Path(t.work_dir).resolve()
    target = (wd / path).resolve()
    if not str(target).startswith(str(wd)) or not target.exists():
        raise HTTPException(404)
    return FileResponse(target)


@router.put("/tasks/{task_id}/files/{path:path}")
async def write_file(
    task_id: str, path: str,
    body: dict[str, Any],
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """覆盖写入工作区文本文件（仅限 .md / .txt / .py）。
    写入 sec_*.md 后自动重组 paper.md。
    body: { "content": "文件内容" }
    """
    t = await task_manager.get_or_load(task_id); _ensure_owner_or_admin(t, user)
    if t.state == TaskState.RUNNING:
        raise HTTPException(400, "任务正在运行，请等待完成后再编辑")

    # 路径安全检查
    wd = Path(t.work_dir).resolve()
    target = (wd / path).resolve()
    if not str(target).startswith(str(wd)):
        raise HTTPException(403, "非法路径")

    allowed_ext = {".md", ".txt", ".py"}
    if target.suffix.lower() not in allowed_ext:
        raise HTTPException(400, f"仅支持编辑 {allowed_ext} 类型文件")

    content = body.get("content", "")
    if not isinstance(content, str):
        raise HTTPException(422, "content 必须为字符串")

    target.write_text(content, encoding="utf-8")

    # sec_*.md 写入后重组 paper.md
    import re as _re
    if _re.match(r"sec_.+\.md$", target.name):
        try:
            import json as _json
            from ..workflow.rewrite import _rebuild_paper
            q_file = wd / "questions.json"
            questions = _json.loads(q_file.read_text(encoding="utf-8")) if q_file.exists() else {}
            ques_count = int(questions.get("ques_count", 1))
            _rebuild_paper(wd, ques_count, questions)
        except Exception as _e:
            pass  # paper.md 重组失败不影响文件保存

    return {"ok": True, "path": path, "size": target.stat().st_size}


# ---------- 导出 ----------

@router.get("/tasks/{task_id}/notebook")
async def download_notebook(task_id: str, user: User = Depends(get_current_user)) -> Any:
    """下载任务的 Jupyter Notebook（.ipynb），包含所有代码执行过程和输出。"""
    t = await task_manager.get_or_load(task_id); _ensure_owner_or_admin(t, user)
    nb_path = Path(t.work_dir) / "notebook.ipynb"
    if not nb_path.exists():
        raise HTTPException(404, detail="Notebook 尚未生成（任务未开始或尚无代码执行）")
    return FileResponse(
        nb_path,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{task_id}_notebook.ipynb"'},
    )


@router.get("/tasks/{task_id}/archive")
async def download_archive(
    task_id: str,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
) -> Any:
    """将工作区所有文件打包为 ZIP 下载（磁盘临时文件，避免大工作区 OOM）。"""
    t = await task_manager.get_or_load(task_id); _ensure_owner_or_admin(t, user)
    import tempfile, zipfile as _zip
    wd = Path(t.work_dir)
    # 临时文件落在工作区父级，避免跨盘 IO；下载完成后由 BackgroundTasks 清理
    tmp_path = Path(tempfile.mkstemp(prefix=f"archive_{task_id}_", suffix=".zip")[1])
    try:
        with _zip.ZipFile(tmp_path, "w", _zip.ZIP_DEFLATED, allowZip64=True) as zf:
            for p in sorted(wd.rglob("*")):
                if p.is_file():
                    try:
                        zf.write(p, p.relative_to(wd))
                    except Exception as e:
                        logger.warning("archive skip {}: {}", p, e)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    def _cleanup(p: Path = tmp_path) -> None:
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass
    background_tasks.add_task(_cleanup)

    return FileResponse(
        tmp_path,
        media_type="application/zip",
        filename=f"{task_id}.zip",
        headers={"Content-Disposition": f'attachment; filename="{task_id}.zip"'},
    )


@router.get("/tasks/{task_id}/export/docx")
async def export_docx(task_id: str, user: User = Depends(get_current_user)) -> Any:
    """将 paper.md 导出为 DOCX（pandoc 优先，降级 python-docx）。"""
    t = await task_manager.get_or_load(task_id); _ensure_owner_or_admin(t, user)
    wd = Path(t.work_dir)
    md_path = wd / "paper.md"
    if not md_path.exists():
        raise HTTPException(404, "paper.md 不存在，任务尚未完成")
    docx_path = wd / "paper_export.docx"
    try:
        from ..exporters import export_paper as _export_docx
        _export_docx(md_path, docx_path)
    except FileNotFoundError:
        raise HTTPException(404, "paper.md 不存在")
    except Exception as e:
        raise HTTPException(500, f"DOCX 导出失败：{e}")
    return FileResponse(
        docx_path, filename="paper.docx",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": 'attachment; filename="paper.docx"'},
    )


@router.get("/tasks/{task_id}/export/pdf")
async def export_pdf(task_id: str, user: User = Depends(get_current_user)) -> Any:
    """将 paper.md 通过 pandoc 导出为 PDF。"""
    t = await task_manager.get_or_load(task_id); _ensure_owner_or_admin(t, user)
    wd = Path(t.work_dir)
    md_path = wd / "paper.md"
    if not md_path.exists():
        raise HTTPException(404, "paper.md 不存在，任务尚未完成")
    pdf_path = wd / "paper_export.pdf"
    try:
        _md_to_pdf(md_path, pdf_path)
    except RuntimeError as e:
        raise HTTPException(501, str(e))
    return FileResponse(
        pdf_path, filename="paper.pdf", media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="paper.pdf"'},
    )


@router.get("/tasks/{task_id}/files/{path:path}/as/ipynb")
async def export_ipynb(task_id: str, path: str, user: User = Depends(get_current_user)) -> Any:
    """将工作区 .py 文件转换为 Jupyter Notebook 格式下载。"""
    t = await task_manager.get_or_load(task_id); _ensure_owner_or_admin(t, user)
    wd = Path(t.work_dir).resolve()
    target = (wd / path).resolve()
    if not str(target).startswith(str(wd)) or not target.exists():
        raise HTTPException(404, "文件不存在")
    if target.suffix.lower() != ".py":
        raise HTTPException(400, "仅支持 .py 文件转换为 .ipynb")
    code = target.read_text(encoding="utf-8")
    nb_json = _py_to_ipynb(code)
    fname = target.stem + ".ipynb"
    return Response(
        content=nb_json,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


def _md_to_pdf(md_path: Path, pdf_path: Path) -> None:
    """pandoc 驱动的 Markdown→PDF，依次尝试可用的 PDF 引擎。"""
    import shutil, subprocess
    if not shutil.which("pandoc"):
        raise RuntimeError("服务器未安装 pandoc，无法导出 PDF")
    engines = [e for e in ["xelatex", "pdflatex", "lualatex", "wkhtmltopdf", "weasyprint"]
               if shutil.which(e)]
    if not engines:
        raise RuntimeError("pandoc 已安装，但未找到 PDF 引擎（需要 xelatex / wkhtmltopdf / weasyprint 之一）")
    for eng in engines:
        cmd = ["pandoc", str(md_path), "-o", str(pdf_path),
               "--standalone", f"--pdf-engine={eng}"]
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=120,
                                    cwd=str(md_path.parent))
            if result.returncode == 0:
                return
        except Exception:
            continue
    raise RuntimeError(f"pandoc 导出失败，尝试了引擎：{engines}")


def _py_to_ipynb(code: str) -> str:
    """将 Python 脚本按 # %% 分割后打包为 notebook JSON。"""
    import re as _re, json as _json
    parts = _re.split(r"\n# ?%%[^\n]*", "\n" + code)
    cells = []
    for part in parts:
        src = part.strip()
        if src:
            cells.append({
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": src,
            })
    if not cells:
        cells = [{"cell_type": "code", "execution_count": None,
                  "metadata": {}, "outputs": [], "source": code}]
    nb = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python", "version": "3.10.0"},
        },
        "cells": cells,
    }
    return _json.dumps(nb, ensure_ascii=False, indent=1)


# ---------- WebSocket ----------
def _ws_is_closed(ws: WebSocket) -> bool:
    from starlette.websockets import WebSocketState
    return (
        ws.client_state != WebSocketState.CONNECTED
        or ws.application_state != WebSocketState.CONNECTED
    )


@router.websocket("/ws/tasks/{task_id}")
async def ws_task(ws: WebSocket, task_id: str) -> None:
    token = ws.query_params.get("token")
    async with AsyncSessionLocal() as session:
        user = await get_user_from_token(token, session)
    if not user:
        await ws.close(code=4401); return
    t = await task_manager.get_or_load(task_id)
    if not t or (t.user_id != user.id and user.role != "admin"):
        await ws.close(code=4403); return

    await ws.accept()
    queue = await bus.subscribe(task_id)
    try:
        while True:
            if _ws_is_closed(ws):
                break
            try:
                # wait_for 让我们可以定期检查 WS 是否已关闭（心跳间隔 20s）
                event = await asyncio.wait_for(queue.get(), timeout=20.0)
            except asyncio.TimeoutError:
                # 发送 ping 保活（空 JSON 忽略即可）
                try:
                    await ws.send_text("{\"type\":\"ping\"}")
                except Exception:
                    break
                continue
            if _ws_is_closed(ws):
                break
            try:
                await ws.send_text(json.dumps(event.to_dict(), ensure_ascii=False, default=str))
            except (WebSocketDisconnect, RuntimeError):
                break
            except Exception as e:
                if "send" in str(e).lower() or "close" in str(e).lower():
                    break
                logger.warning("ws send error: {}", e)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning("ws error: {}", e)
    finally:
        await bus.unsubscribe(task_id, queue)
        logger.debug("ws closed | task={}", task_id)
