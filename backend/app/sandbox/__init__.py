from pathlib import Path

from .jupyter import JupyterSandbox, ExecResult

__all__ = ["JupyterSandbox", "ExecResult", "create_sandbox"]


def create_sandbox(task_id: str, work_dir: Path) -> "JupyterSandbox":
    """根据 SANDBOX_KIND 配置返回对应的沙箱实例。

    - local（默认）：本地 Jupyter Kernel
    - e2b：E2B 云端容器（需配置 E2B_API_KEY）
    """
    from ..core.config import get_settings
    kind = get_settings().sandbox_kind
    if kind == "e2b":
        from .e2b import E2BSandbox
        return E2BSandbox(task_id, work_dir)  # type: ignore[return-value]
    return JupyterSandbox(task_id, work_dir)
