"""轻量级 .ipynb 录制器。

不依赖 nbformat，直接构造符合 nbformat v4 规范的 JSON。
随 JupyterSandbox 生命周期存在，记录每个代码 cell 的输入与输出，
任务结束后写入 notebook.ipynb，用户可下载后在本地 Jupyter 复现。
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any


def _cid() -> str:
    """生成 16 字符的 cell id（符合 nbformat 要求）。"""
    return uuid.uuid4().hex[:16]


class NotebookRecorder:
    """记录执行过程并序列化为 .ipynb。

    典型调用顺序（由 JupyterSandbox 驱动）：
        recorder.add_phase_marker("EDA")          # 分节标题
        recorder.record_cell(code)                # 开始新 cell
        recorder.record_stdout(text)              # stdout 追加
        recorder.record_image(b64, "image/png")   # 图片追加
        recorder.record_error(ename, eval, tb)    # 错误追加
        recorder.flush(path)                      # 持久化
    """

    def __init__(self) -> None:
        self._cells: list[dict[str, Any]] = []
        self._exec_count: int = 0
        self._last_code_idx: int = -1   # _cells 中最后一个 code cell 的下标

    # ── 分节 ──────────────────────────────────────────────────────────────────

    def add_phase_marker(self, label: str) -> None:
        """插入 Markdown 分节 cell，形如 `## ▶ EDA 探索性数据分析`。"""
        self._cells.append({
            "cell_type": "markdown",
            "id": _cid(),
            "metadata": {},
            "source": f"## ▶ {label}",
        })

    # ── 代码 cell ─────────────────────────────────────────────────────────────

    def record_cell(self, code: str) -> None:
        """开始记录新代码 cell（在向 kernel 发送执行请求之前调用）。"""
        self._exec_count += 1
        self._last_code_idx = len(self._cells)
        self._cells.append({
            "cell_type": "code",
            "id": _cid(),
            "metadata": {},
            "source": code,
            "outputs": [],
            "execution_count": self._exec_count,
        })

    def _last_cell(self) -> dict[str, Any] | None:
        if 0 <= self._last_code_idx < len(self._cells):
            return self._cells[self._last_code_idx]
        return None

    # ── 输出追加 ──────────────────────────────────────────────────────────────

    def record_stdout(self, text: str) -> None:
        cell = self._last_cell()
        if cell is None:
            return
        outputs = cell["outputs"]
        # 连续 stdout 合并到同一 stream output，避免碎片化
        if outputs and outputs[-1].get("output_type") == "stream" \
                and outputs[-1].get("name") == "stdout":
            outputs[-1]["text"] += text
        else:
            outputs.append({"output_type": "stream", "name": "stdout", "text": text})

    def record_stderr(self, text: str) -> None:
        cell = self._last_cell()
        if cell is None:
            return
        outputs = cell["outputs"]
        if outputs and outputs[-1].get("output_type") == "stream" \
                and outputs[-1].get("name") == "stderr":
            outputs[-1]["text"] += text
        else:
            outputs.append({"output_type": "stream", "name": "stderr", "text": text})

    def record_image(self, b64: str, mime: str) -> None:
        """记录图片输出（display_data）。mime 形如 'image/png'。"""
        cell = self._last_cell()
        if cell is None:
            return
        cell["outputs"].append({
            "output_type": "display_data",
            "metadata": {},
            "data": {mime: b64, "text/plain": "<Figure>"},
        })

    def record_result(self, text: str) -> None:
        """记录 execute_result（表达式最终值）。"""
        cell = self._last_cell()
        if cell is None:
            return
        cell["outputs"].append({
            "output_type": "execute_result",
            "execution_count": self._exec_count,
            "metadata": {},
            "data": {"text/plain": text},
        })

    def record_error(self, ename: str, evalue: str, traceback: list[str]) -> None:
        """记录错误输出（error type output）。"""
        cell = self._last_cell()
        if cell is None:
            return
        cell["outputs"].append({
            "output_type": "error",
            "ename": ename,
            "evalue": evalue,
            "traceback": traceback,
        })

    # ── 持久化 ────────────────────────────────────────────────────────────────

    def flush(self, path: Path) -> None:
        """将当前所有 cell 写入 .ipynb 文件（增量覆盖写，始终保持文件可用）。"""
        nb = {
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {
                "kernelspec": {
                    "display_name": "Python 3",
                    "language": "python",
                    "name": "python3",
                },
                "language_info": {"name": "python", "version": "3"},
            },
            "cells": self._cells,
        }
        path.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
