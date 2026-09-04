# tools/ — 工具实现层

## 职责
提供内置工具的具体实现和 MCP 桥接能力。

**注**：工具契约（Tool / ToolRegistry / ToolResult / @tool / registry 单例）归属
`engine/tool_contracts.py`（接口与实现分离，engine 不依赖本包）。

## 包含文件
| 文件 | 职责 |
|------|------|
| `builtins.py` | 内置工具（rag_retrieve / get_setting / quality_check 等 + 项目上下文注入） |
| `mcp_bridge.py` | MCP 桥接器（MCPBridge - 连接外部 MCP 工具） |

## 依赖规则
- 依赖 engine/（工具契约）、story/、rag/、quality/（懒加载）

## 被依赖
- agents/ (WriterAgent 使用工具)
- workflows/ (agentic_write)
- service/ (MCPBridge)