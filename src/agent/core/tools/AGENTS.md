# AGENTS.md - core/tools/ Tool 实现层

## 职责

提供具体工具实现与 MCP 桥接，供 AgentLoop 使用。

## 核心模块

| 文件 | 导出 | 作用 |
|------|------|------|
| `builtins.py` | `set_project_context` 等 | 内置工具实现（rag_retrieve / get_setting / count_words / quality_check / foreshadow_read / export_chapters） |
| `mcp_bridge.py` | `MCPBridge` | MCP 桥接 |

## 设计说明

- 工具契约（`Tool`/`ToolRegistry`/`@tool`/`registry` 单例）归属 `engine/tool_contracts.py`
- 本包只提供具体实现与 MCP 桥接（接口与实现分离）
- 导入本包即注册全部内置工具

## 依赖规则

- 依赖 engine（tool_contracts）和 llm
- 不依赖上层业务包