"""HTTP + WebSocket 路由（多用户 + 模型个性化）。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import get_current_user, get_user_from_token
from ..core.events import bus
from ..core.logging import logger
from ..db import AsyncSessionLocal, User, get_session
from ..services.model_service import DEFAULT_AGENTS, list_configs, resolve_effective, upsert_config
from ..services.usage_service import stats_for_task
from ..tasks import TaskState, task_manager
from ..workflow import run_workflow
from .schemas import TaskResponse

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
    return {"ok": True, "service": "mathoi-agent"}


# ---------- 模型配置（当前用户视角） ----------
class UserModelUpdate(BaseModel):
    agent: str = Field(..., pattern=r"^(default|modeler|coder|writer)$")
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
@router.post("/tasks", response_model=TaskResponse)
async def create_task(
    title: str = Form(...),
    problem: str = Form(...),
    files: list[UploadFile] = File(default_factory=list),
    user: User = Depends(get_current_user),
) -> TaskResponse:
    if not title or len(problem) < 10:
        raise HTTPException(400, "title 与 problem（至少 10 字）必填")
    t = await task_manager.create(user_id=user.id, title=title, problem=problem, data_files=[])

    saved: list[str] = []
    work_dir = Path(t.work_dir)
    for f in files:
        if not f.filename:
            continue
        safe_name = Path(f.filename).name
        dst = work_dir / safe_name
        dst.write_bytes(await f.read())
        saved.append(safe_name)
    t.data_files = saved
    await task_manager.update_data_files(t.task_id, saved)

    handle = asyncio.create_task(run_workflow(t.task_id))
    task_manager.attach_handle(t.task_id, handle)
    return _to_response(t)


@router.get("/tasks", response_model=list[TaskResponse])
async def list_tasks(user: User = Depends(get_current_user)) -> list[TaskResponse]:
    return [_to_response(t) for t in task_manager.list_for_user(user.id)]


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task(task_id: str, user: User = Depends(get_current_user)) -> TaskResponse:
    t = task_manager.get(task_id); _ensure_owner_or_admin(t, user)
    return _to_response(t)


@router.get("/tasks/{task_id}/events")
async def get_history(task_id: str, user: User = Depends(get_current_user)) -> list[dict[str, Any]]:
    t = task_manager.get(task_id); _ensure_owner_or_admin(t, user)
    return [e.to_dict() for e in bus.history(task_id)]


@router.post("/tasks/{task_id}/pause")
async def pause(task_id: str, user: User = Depends(get_current_user)) -> dict[str, Any]:
    t = task_manager.get(task_id); _ensure_owner_or_admin(t, user)
    await task_manager.pause(task_id)
    return {"ok": True}


@router.post("/tasks/{task_id}/resume")
async def resume(task_id: str, user: User = Depends(get_current_user)) -> dict[str, Any]:
    t = task_manager.get(task_id); _ensure_owner_or_admin(t, user)
    await task_manager.resume(task_id)
    return {"ok": True}


@router.post("/tasks/{task_id}/cancel")
async def cancel(task_id: str, user: User = Depends(get_current_user)) -> dict[str, Any]:
    t = task_manager.get(task_id); _ensure_owner_or_admin(t, user)
    await task_manager.cancel(task_id)
    return {"ok": True}


class HITLReply(BaseModel):
    action: str = "approve"
    edited_plan: Optional[str] = None
    feedback: Optional[str] = None


@router.post("/tasks/{task_id}/hitl")
async def hitl_reply(task_id: str, body: HITLReply, user: User = Depends(get_current_user)) -> dict[str, Any]:
    t = task_manager.get(task_id); _ensure_owner_or_admin(t, user)
    await task_manager.reply_hitl(task_id, body.model_dump())
    return {"ok": True}


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str, user: User = Depends(get_current_user)) -> dict[str, Any]:
    t = task_manager.get(task_id); _ensure_owner_or_admin(t, user)
    await task_manager.delete(task_id)
    return {"ok": True}


# ---------- 任务 token 用量 ----------
@router.get("/tasks/{task_id}/usage")
async def get_task_usage(task_id: str, user: User = Depends(get_current_user)) -> dict[str, Any]:
    t = task_manager.get(task_id); _ensure_owner_or_admin(t, user)
    return await stats_for_task(task_id)


# ---------- 文件 ----------
@router.get("/tasks/{task_id}/files")
async def list_files(task_id: str, user: User = Depends(get_current_user)) -> dict[str, Any]:
    t = task_manager.get(task_id); _ensure_owner_or_admin(t, user)
    wd = Path(t.work_dir)
    files = []
    for p in sorted(wd.rglob("*")):
        if p.is_file():
            files.append({"name": str(p.relative_to(wd)).replace("\\", "/"), "size": p.stat().st_size})
    return {"work_dir": str(wd), "files": files}


@router.get("/tasks/{task_id}/files/{path:path}")
async def get_file(task_id: str, path: str, user: User = Depends(get_current_user)) -> Any:
    t = task_manager.get(task_id); _ensure_owner_or_admin(t, user)
    wd = Path(t.work_dir).resolve()
    target = (wd / path).resolve()
    if not str(target).startswith(str(wd)) or not target.exists():
        raise HTTPException(404)
    return FileResponse(target)


# ---------- 导出 ----------

@router.get("/tasks/{task_id}/archive")
async def download_archive(task_id: str, user: User = Depends(get_current_user)) -> Any:
    """将工作区所有文件打包为 ZIP 下载。"""
    t = task_manager.get(task_id); _ensure_owner_or_admin(t, user)
    import io, zipfile as _zip
    wd = Path(t.work_dir)
    buf = io.BytesIO()
    with _zip.ZipFile(buf, "w", _zip.ZIP_DEFLATED) as zf:
        for p in sorted(wd.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(wd))
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{task_id}.zip"'},
    )


@router.get("/tasks/{task_id}/export/pdf")
async def export_pdf(task_id: str, user: User = Depends(get_current_user)) -> Any:
    """将 paper.md 通过 pandoc 导出为 PDF。"""
    t = task_manager.get(task_id); _ensure_owner_or_admin(t, user)
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
    t = task_manager.get(task_id); _ensure_owner_or_admin(t, user)
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
@router.websocket("/ws/tasks/{task_id}")
async def ws_task(ws: WebSocket, task_id: str) -> None:
    token = ws.query_params.get("token")
    async with AsyncSessionLocal() as session:
        user = await get_user_from_token(token, session)
    if not user:
        await ws.close(code=4401); return
    t = task_manager.get(task_id)
    if not t or (t.user_id != user.id and user.role != "admin"):
        await ws.close(code=4403); return

    await ws.accept()
    queue = await bus.subscribe(task_id)
    try:
        while True:
            event = await queue.get()
            await ws.send_text(json.dumps(event.to_dict(), ensure_ascii=False, default=str))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning("ws error: {}", e)
    finally:
        await bus.unsubscribe(task_id, queue)
