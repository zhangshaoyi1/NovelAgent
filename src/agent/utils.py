"""通用工具函数（含 CLI 渲染层工具）

下沉说明（2026-08-29）：纯标准库工具 ``parse_llm_json`` / ``safe_remove`` /
``chunk_text`` 已下沉到 ``agent.base.utils``（保证 base 层零外部依赖，供
core/base 等底层模块复用）。本模块保留 `make_quiet_console`（依赖 rich，属于
CLI 渲染层职责），并对下沉函数做向后兼容再导出。
"""

from __future__ import annotations

import sys
from typing import Any

from agent.base.utils import chunk_text, parse_llm_json, safe_remove  # noqa: F401  # 向后兼容再导出


def make_quiet_console() -> "Console":
    """构造一个把 rich 输出导向 stderr 的 Console（--json 模式用）。

    将工作流内部大量 console.print 重定向到 stderr，避免污染 stdout 的 JSON 输出。

    Returns:
        输出目标为 sys.stderr 的 rich.Console 实例。
    """
    from rich.console import Console

    return Console(file=sys.stderr, highlight=False, markup=False, width=200)


__all__ = ["parse_llm_json", "safe_remove", "chunk_text", "make_quiet_console"]
