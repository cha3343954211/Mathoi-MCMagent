from typing import Any, Optional

from pydantic import BaseModel, Field


class CreateTaskRequest(BaseModel):
    title: str = Field(..., max_length=200)
    problem: str = Field(..., min_length=10)


class TaskResponse(BaseModel):
    task_id: str
    user_id: int
    title: str
    problem: str
    state: str
    phase: str
    error: str
    work_dir: str
    data_files: list[str]
    created_at: float
    updated_at: float
    hitl_request: Optional[dict[str, Any]] = None


class HITLReplyRequest(BaseModel):
    action: str = Field(..., description="approve | edit | redo | abort")
    edited_plan: Optional[str] = None
    feedback: Optional[str] = None


class ModelConfigItem(BaseModel):
    agent: str
    model: str
    base_url: str
    has_api_key: bool
    temperature: float


class ModelConfigResponse(BaseModel):
    backend: str
    items: list[ModelConfigItem]


class UpdateModelConfigRequest(BaseModel):
    backend: Optional[str] = None
    overrides: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="agent -> { model, base_url, api_key, temperature }",
    )
