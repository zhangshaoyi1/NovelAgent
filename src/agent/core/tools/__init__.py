"""Tool 实现层（Phase 0）

导入本包即注册全部内置工具：``from agent.core.tools import registry`` 后
``registry.names()`` 可见 rag_retrieve / get_setting / count_words /
quality_check / foreshadow_read / export_chapters。

工具契约（Tool / ToolRegistry / @tool / registry 单例）归属
``engine/tool_contracts.py``（接口与实现分离，engine 不依赖本包），
本包只提供具体实现与 MCP 桥接。
"""

from __future__ import annotations

from agent.core.engine.tool_contracts import (
    Tool,
    ToolResult,
    ToolRegistry,
    registry,
    tool,
)
from agent.core.tools import builtins  # noqa: F401 — 触发内置工具注册
from agent.core.tools.mcp_bridge import MCPBridge

__all__ = ["Tool", "ToolResult", "ToolRegistry", "registry", "tool", "builtins", "MCPBridge"]
