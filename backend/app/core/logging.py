"""统一日志配置（loguru）。"""
from __future__ import annotations

import sys

from loguru import logger

from .config import get_settings


def setup_logging() -> None:
    settings = get_settings()
    logger.remove()
    logger.add(
        sys.stderr,
        level=settings.log_level,
        format=(
            "<green>{time:HH:mm:ss.SSS}</green> | "
            "<level>{level: <7}</level> | "
            "<cyan>{name}:{function}:{line}</cyan> | "
            "<level>{message}</level>"
        ),
        enqueue=False,
    )


__all__ = ["logger", "setup_logging"]
