"""ORM 模型。SQLite 默认，PostgreSQL 兼容。"""
from __future__ import annotations

import enum
import time
import uuid
from typing import Optional

from sqlalchemy import (
    Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class UserRole(str, enum.Enum):
    USER = "user"
    PRO = "pro"
    ADMIN = "admin"


def _now() -> float:
    return time.time()


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), default=UserRole.USER.value, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # 是否使用管理员配置的默认模型；false 时走用户自定义
    use_default_model: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[float] = mapped_column(Float, default=_now, nullable=False)
    last_login: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    tasks: Mapped[list["TaskRecord"]] = relationship(back_populates="owner", cascade="all,delete-orphan")

    @property
    def is_admin(self) -> bool:
        return self.role == UserRole.ADMIN.value

    @property
    def is_pro(self) -> bool:
        return self.role in (UserRole.PRO.value, UserRole.ADMIN.value)


class TaskRecord(Base):
    __tablename__ = "tasks"

    task_id: Mapped[str] = mapped_column(String(32), primary_key=True,
                                         default=lambda: uuid.uuid4().hex[:12])
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                                         index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    problem: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    phase: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    work_dir: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    data_files: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    image_files: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    created_at: Mapped[float] = mapped_column(Float, default=_now, nullable=False, index=True)
    updated_at: Mapped[float] = mapped_column(Float, default=_now, nullable=False)

    owner: Mapped["User"] = relationship(back_populates="tasks")


class ModelPreset(Base):
    """管理员配置的模型预设（可多条，供用户选择）。
    - agent = 'all'：适用于所有 Agent
    - agent = 'modeler'/'coder'/'writer'/'default'：仅特定 Agent 可见
    """
    __tablename__ = "model_presets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)          # 显示名，如 "DeepSeek Chat"
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)  # 可选说明
    agent: Mapped[str] = mapped_column(String(256), default="all", nullable=False)  # 'all' | 逗号分隔多值如 'modeler,coder'
    backend: Mapped[str] = mapped_column(String(16), default="openai", nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    base_url: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    api_key_enc: Mapped[str] = mapped_column(Text, default="", nullable=False)  # Fernet 密文
    temperature: Mapped[float] = mapped_column(Float, default=0.2, nullable=False)
    max_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=None)
    price_prompt_per_1k: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    price_completion_per_1k: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # 全局默认预设
    pro_only: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)    # 仅管理员/Pro用户可用
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[float] = mapped_column(Float, default=_now, nullable=False)
    updated_at: Mapped[float] = mapped_column(Float, default=_now, nullable=False)


class ModelConfigRow(Base):
    """模型配置。
    - owner_id IS NULL：保留作终极兜底（env 配置），不再通过 UI 直接管理
    - owner_id = user.id：用户自定义
    - agent ∈ {default, modeler, coder, writer}
      default 作为 fallback：当某 agent 没独立配置时用 default。
    - selected_preset_id：用户选择的预设 ID，非 NULL 时优先使用预设。
    """
    __tablename__ = "model_configs"
    __table_args__ = (
        UniqueConstraint("owner_id", "agent", name="uq_model_owner_agent"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    agent: Mapped[str] = mapped_column(String(32), nullable=False)
    backend: Mapped[str] = mapped_column(String(16), default="openai", nullable=False)
    model: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    base_url: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    api_key_enc: Mapped[str] = mapped_column(Text, default="", nullable=False)  # Fernet 密文
    temperature: Mapped[float] = mapped_column(Float, default=0.2, nullable=False)
    # 计费单价（USD per 1K tokens），管理员可设置；0 表示不计费
    price_prompt_per_1k: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    price_completion_per_1k: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    # 单次调用最大输出 token 数（None 表示不限制，由模型默认）
    max_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=None)
    # 用户选择的预设 ID（非 NULL 时走预设，忽略自定义字段）
    selected_preset_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=None)
    updated_at: Mapped[float] = mapped_column(Float, default=_now, nullable=False)


class EventRecord(Base):
    """任务事件持久化记录（除流式 chunk 外全量保存）。随任务删除级联清理。"""
    __tablename__ = "task_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    task_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("tasks.task_id", ondelete="CASCADE"), index=True, nullable=False
    )
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    agent: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    payload: Mapped[str] = mapped_column(Text, default="{}", nullable=False)   # JSON
    timestamp: Mapped[float] = mapped_column(Float, nullable=False)


class SystemSetting(Base):
    """系统级键值配置，供管理员通过 UI 修改（如 OpenAlex email）。
    优先级：DB 值 > 环境变量 > 代码默认值。
    """
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="", nullable=False)
    updated_at: Mapped[float] = mapped_column(Float, default=_now, nullable=False)


class UsageRecord(Base):
    """每次 LLM 调用的计量记录。"""
    __tablename__ = "usage_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    task_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("tasks.task_id", ondelete="SET NULL"), index=True, nullable=True
    )
    agent: Mapped[str] = mapped_column(String(32), nullable=False)
    backend: Mapped[str] = mapped_column(String(16), default="openai", nullable=False)
    model: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    # True 表示该次调用使用了管理员维护的默认模型
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    ok: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[float] = mapped_column(Float, default=_now, nullable=False, index=True)
