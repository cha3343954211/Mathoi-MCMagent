"""共享 fixture：临时工作区、最简 Settings 环境变量。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Iterator

import pytest

# 让 `app.*` 包可被 pytest 发现（无 src 布局）
_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# 关键：测试不应触碰真实数据库 / 默认 admin 密码 / Fernet 密钥
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("JWT_SECRET", "test-secret-please-change-" + "x" * 32)
os.environ.setdefault("DEFAULT_ADMIN_PASSWORD", "test-admin-password")
os.environ.setdefault("LOG_LEVEL", "WARNING")


@pytest.fixture
def tmp_workspace() -> Iterator[Path]:
    """临时工作区目录，测试结束自动清理。"""
    with tempfile.TemporaryDirectory(prefix="mathoi_test_") as d:
        yield Path(d)
