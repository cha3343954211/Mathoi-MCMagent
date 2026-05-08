"""配置系统。

支持全局默认 + 各 Agent 独立覆盖（多模型协同）。
任意未在 Agent 段配置的字段，自动回退到 DEFAULT_*。
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelConfig(BaseSettings):
    """单一模型配置。"""

    model: str = ""
    base_url: str = ""
    api_key: str = ""
    temperature: float = 0.3

    def merged_with(self, fallback: "ModelConfig") -> "ModelConfig":
        """缺省字段回退到 fallback。"""
        return ModelConfig(
            model=self.model or fallback.model,
            base_url=self.base_url or fallback.base_url,
            api_key=self.api_key or fallback.api_key,
            temperature=self.temperature if self.model else fallback.temperature,
        )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # 应用
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"
    workspace_dir: str = "./workspace"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Redis
    redis_url: str = ""

    # 数据库
    database_url: str = "sqlite+aiosqlite:///./mathoi.db"

    # 认证
    jwt_secret: str = "change-me-please"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24 * 7
    allow_register: bool = True
    default_admin_username: str = "admin"
    default_admin_password: str = "admin123"
    default_admin_email: str = "admin@example.com"

    # LLM 后端选择
    llm_backend: Literal["openai", "litellm"] = "openai"

    # 默认模型
    default_model: str = ""
    default_base_url: str = ""
    default_api_key: str = ""
    default_temperature: float = 0.3

    # Modeler
    modeler_model: str = ""
    modeler_base_url: str = ""
    modeler_api_key: str = ""
    modeler_temperature: float = 0.2

    # Coder
    coder_model: str = ""
    coder_base_url: str = ""
    coder_api_key: str = ""
    coder_temperature: float = 0.1

    # Writer
    writer_model: str = ""
    writer_base_url: str = ""
    writer_api_key: str = ""
    writer_temperature: float = 0.5

    # 沙箱
    sandbox_timeout: int = 120
    sandbox_kernel: str = "python3"
    sandbox_kind: Literal["local", "e2b"] = "local"  # 执行环境选择
    e2b_api_key: str = ""                             # E2B 云端沙算 API Key

    # 工作流
    max_coder_iterations: int = 8
    max_revision_rounds: int = 2
    max_task_hours: float = 6.0   # 单任务最长运行时间（小时），超时强制 FAILED
    hitl_timeout_hours: float = 24.0  # HITL 等待上限（小时），超时自动 approve
    daily_token_quota: int = 0        # 用户每日 token 上限（0 = 不限）
    max_concurrent_tasks: int = 4     # 同时运行任务上限（0 = 不限）；8C8G 推荐 4

    # 上传限制（防止恶意/误操作 OOM）
    max_upload_file_mb: int = 100     # 单文件最大体积（MB）
    max_upload_total_mb: int = 500    # 单次任务上传总和上限（MB）
    max_upload_files: int = 20        # 单次任务最多文件数

    # OpenAlex 学术搜索（Writer 引用文献用）
    openalex_email: str = ""   # polite pool 必填，空则跳过文献搜索

    # 联网搜索（Coder web_search 工具）
    search_provider: Literal["duckduckgo", "searxng"] = "duckduckgo"
    searxng_base_url: str = ""          # e.g. http://127.0.0.1:8080
    searxng_timeout: float = 8.0        # 单次请求超时秒数
    search_max_results: int = 6         # 默认返回条数

    # ---------- 派生方法 ----------
    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def workspace_path(self) -> Path:
        p = Path(self.workspace_dir).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def default_config(self) -> ModelConfig:
        return ModelConfig(
            model=self.default_model,
            base_url=self.default_base_url,
            api_key=self.default_api_key,
            temperature=self.default_temperature,
        )

    def agent_config(self, agent: str) -> ModelConfig:
        """根据 Agent 名取配置，未配置则回退到默认。"""
        agent = agent.lower()
        raw = ModelConfig(
            model=getattr(self, f"{agent}_model", "") or "",
            base_url=getattr(self, f"{agent}_base_url", "") or "",
            api_key=getattr(self, f"{agent}_api_key", "") or "",
            temperature=getattr(self, f"{agent}_temperature", self.default_temperature),
        )
        return raw.merged_with(self.default_config)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
