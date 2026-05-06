"""异步 SQLAlchemy 引擎与 session。"""
from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..core.config import get_settings
from ..core.logging import logger
from .models import Base, User, UserRole

_settings = get_settings()

_engine = create_async_engine(_settings.database_url, echo=False, future=True)
AsyncSessionLocal = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # 列迁移：为旧库补充新列（SQLite 不支持 IF NOT EXISTS，忽略异常）
        for ddl in [
            "ALTER TABLE model_configs ADD COLUMN max_tokens INTEGER",
            "ALTER TABLE model_configs ADD COLUMN selected_preset_id INTEGER",
            "ALTER TABLE model_presets ADD COLUMN is_default BOOLEAN NOT NULL DEFAULT 0",
            "ALTER TABLE model_presets ADD COLUMN pro_only BOOLEAN NOT NULL DEFAULT 0",
        ]:
            try:
                await conn.exec_driver_sql(ddl)
            except Exception:
                pass  # 列已存在则忽略

    # 默认管理员
    from ..auth.security import hash_password
    from sqlalchemy import select
    async with AsyncSessionLocal() as s:
        exists = (await s.execute(select(User).where(User.role == UserRole.ADMIN.value))).scalar_one_or_none()
        if not exists:
            admin = User(
                username=_settings.default_admin_username,
                email=_settings.default_admin_email,
                hashed_password=hash_password(_settings.default_admin_password),
                role=UserRole.ADMIN.value, is_active=True,
                use_default_model=True,
            )
            s.add(admin)
            await s.commit()
            logger.warning("默认管理员已创建：{} / {} —— 请尽快登录修改密码",
                           _settings.default_admin_username, _settings.default_admin_password)

        # 首次播种全局默认模型（从 env 读）
        from ..services.model_service import seed_defaults_if_empty
        env_defaults: dict[str, dict] = {}
        for agent in ["default", "modeler", "coder", "writer"]:
            cfg = _settings.agent_config(agent)
            env_defaults[agent] = {
                "backend": _settings.llm_backend,
                "model": cfg.model,
                "base_url": cfg.base_url,
                "api_key": cfg.api_key,
                "temperature": cfg.temperature,
            }
        await seed_defaults_if_empty(s, env_defaults)
        await s.commit()


async def get_session() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        yield session
