"""MathoiAgent FastAPI 入口（多用户认证版）。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import asyncio
import time

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from collections import defaultdict, deque

from .api import router as api_router
from .api.admin import router as admin_router
from .auth import auth_router
from .core.config import get_settings
from .core.events import bus as event_bus
from .core.logging import logger, setup_logging
from .db import init_db
from .tasks import task_manager

# ── 滑动窗口限速（内存，单进程）────────────────────────────────────────────────
# 规则：(路径前缀, 窗口秒数, 最大请求数)
_RATE_RULES = [
    ("/api/auth/login",    60, 10),   # 登录：10次/分钟，防暴力破解
    ("/api/auth/register", 60, 5),    # 注册：5次/分钟
    ("/api/tasks",         60, 20),   # 创建任务（POST）：20次/分钟
    ("/api/",              60, 200),  # 全局兜底：200次/分钟
]
# ip -> deque[(timestamp, path_prefix)]
_rate_window: dict[str, deque] = defaultdict(deque)


def _get_client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def _rate_limit_middleware(request: Request, call_next):
    # 管理后台和静态资源不限速
    path = request.url.path
    if path.startswith("/api/admin") or not path.startswith("/api/"):
        return await call_next(request)

    ip = _get_client_ip(request)
    now = time.time()
    q = _rate_window[ip]

    # 淘汰过期记录（超过最大窗口 60s）
    while q and now - q[0][0] > 60:
        q.popleft()

    # 匹配最严格规则
    for prefix, window, limit in _RATE_RULES:
        if path.startswith(prefix):
            # 统计该窗口内对应前缀的请求数
            count = sum(1 for ts, pp in q if now - ts <= window and pp == prefix)
            if count >= limit:
                logger.warning("Rate limit hit: ip={} path={} count={}/{}", ip, path, count, limit)
                return JSONResponse(
                    status_code=429,
                    content={"detail": f"请求过于频繁，请 {window} 秒后重试"},
                    headers={"Retry-After": str(window)},
                )
            break  # 只匹配第一条规则

    q.append((now, next((p for p, _, _ in _RATE_RULES if path.startswith(p)), "/api/")))
    return await call_next(request)

# 前端 dist 目录（与 backend 同级的 frontend/dist）
_DIST = Path(__file__).parent.parent.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    settings = get_settings()
    logger.info(
        "MathoiAgent starting | backend={} workspace={} db={}",
        settings.llm_backend, settings.workspace_path,
        settings.database_url.split("@")[-1],
    )
    await init_db()
    await task_manager.init()
    event_bus.start_flush_worker()          # 启动事件批量写入后台任务
    yield
    # 关闭：停止 flush worker（最终刷写剩余事件）
    if event_bus._flush_task and not event_bus._flush_task.done():
        event_bus._flush_task.cancel()
        try:
            import asyncio
            await asyncio.wait_for(event_bus._flush_task, timeout=3.0)
        except Exception:
            pass
    await task_manager.close()
    logger.info("MathoiAgent stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="MathoiAgent", version="0.2.0", lifespan=lifespan)
    origins = settings.cors_origin_list
    # "*" 通配符时不能同时开 credentials（浏览器安全限制）
    wildcard = origins == ["*"]
    app.middleware("http")(_rate_limit_middleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=not wildcard,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
    )
    # 全局未捕获异常：返回 JSON 而不是 500 HTML 页面
    @app.exception_handler(Exception)
    async def _global_exc_handler(request: Request, exc: Exception):
        logger.exception("Unhandled exception on {}: {}", request.url.path, exc)
        return JSONResponse(
            status_code=500,
            content={"detail": "服务器内部错误，请稍后重试"},
        )

    # 路由注册顺序：auth -> admin -> 业务
    app.include_router(auth_router, prefix="/api")
    app.include_router(admin_router, prefix="/api")
    app.include_router(api_router, prefix="/api")

    # 静态资源（js/css/fonts 等带 hash 的文件）
    if _DIST.exists():
        app.mount("/assets", StaticFiles(directory=str(_DIST / "assets")), name="assets")

        # SPA fallback：所有非 /api 路径均返回 index.html
        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str):
            index = _DIST / "index.html"
            return FileResponse(str(index))

    return app


app = create_app()


def main() -> None:
    s = get_settings()
    uvicorn.run("app.main:app", host=s.app_host, port=s.app_port, reload=False, log_level=s.log_level.lower())


if __name__ == "__main__":
    main()
