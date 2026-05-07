"""MathoiAgent FastAPI 入口（多用户认证版）。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api import router as api_router
from .api.admin import router as admin_router
from .auth import auth_router
from .core.config import get_settings
from .core.events import bus as event_bus
from .core.logging import logger, setup_logging
from .db import init_db
from .tasks import task_manager

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
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
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
