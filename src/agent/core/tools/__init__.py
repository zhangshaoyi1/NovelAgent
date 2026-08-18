"""Tool 层（Phase 0）

导入本包即注册全部内置工具：``from agent.core.tools import registry`` 后
``registry.names()`` 可见 rag_retrieve / get_setting / count_words /
quality_check / foreshadow_read / export_chapters。
"""

from __future__ import annotations

from agent.core.tools.base import (
    Tool,
    ToolResult,
    ToolRegistry,
    registry,
    tool,
)
from agent.core.tools import builtins  # noqa: F401 — 触发内置工具注册
from agent.core.tools.mcp_bridge import MCPBridge

__all__ = ["Tool", "ToolResult", "ToolRegistry", "registry", "tool", "builtins", "MCPBridge"]
