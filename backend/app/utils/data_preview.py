"""数据文件快速预扫：提取 shape / dtypes / head(5) / 缺失率，生成 Markdown 摘要。

支持格式：CSV / TSV / Excel(.xlsx .xls) / JSON(lines / array)
不支持的格式返回 None（上游静默跳过）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..core.logging import logger


def _fmt_null(rate: float) -> str:
    if rate == 0:
        return "0%"
    if rate < 0.001:
        return "<0.1%"
    return f"{rate:.1%}"


def preview_file(path: Path, max_rows: int = 5) -> Optional[str]:
    """返回 Markdown 格式的数据摘要，失败或不支持则返回 None。"""
    suffix = path.suffix.lower()
    try:
        import pandas as pd  # type: ignore

        if suffix in (".csv", ".tsv", ".txt"):
            sep = "\t" if suffix == ".tsv" else None
            df = pd.read_csv(path, sep=sep, nrows=1000, encoding_errors="replace")
        elif suffix in (".xlsx", ".xls"):
            df = pd.read_excel(path, nrows=1000)
        elif suffix == ".json":
            try:
                df = pd.read_json(path, lines=True)
            except Exception:
                df = pd.read_json(path)
        else:
            return None

        rows, cols = df.shape
        lines: list[str] = [
            f"### 数据文件：`{path.name}`",
            f"- 行数（预览前 1000 行）：**{rows}**",
            f"- 列数：**{cols}**",
            "",
            "**列信息（dtype · 缺失率 · 示例值）：**",
        ]
        for col in df.columns:
            series = df[col]
            dtype = str(series.dtype)
            null_rate = series.isna().mean()
            # 示例：取前几个非空值
            samples = series.dropna().head(3).tolist()
            sample_str = ", ".join(repr(v) for v in samples) if samples else "—"
            lines.append(
                f"- `{col}` ({dtype}，缺失 {_fmt_null(null_rate)})：{sample_str}"
            )

        # head(5) 转 Markdown 表格
        if max_rows > 0 and not df.empty:
            head = df.head(max_rows).fillna("")
            lines.append("")
            lines.append(f"**前 {min(max_rows, len(df))} 行：**")
            header = "| " + " | ".join(str(c) for c in head.columns) + " |"
            sep_row = "| " + " | ".join("---" for _ in head.columns) + " |"
            lines += [header, sep_row]
            for _, row in head.iterrows():
                lines.append("| " + " | ".join(str(v)[:40] for v in row) + " |")

        return "\n".join(lines)
    except ImportError:
        logger.debug("pandas not installed, skip data preview for {}", path.name)
        return None
    except Exception as e:
        logger.debug("data preview failed for {}: {}", path.name, e)
        return None


def build_data_preview(work_dir: Path, data_files: list[str]) -> str:
    """为所有数据文件生成联合预扫摘要（Markdown），空则返回空串。"""
    parts: list[str] = []
    for fname in data_files:
        p = work_dir / fname
        if not p.exists():
            continue
        md = preview_file(p)
        if md:
            parts.append(md)
    if not parts:
        return ""
    return (
        "## 📊 数据文件预扫（供建模参考）\n\n"
        + "\n\n---\n\n".join(parts)
        + "\n\n"
    )
