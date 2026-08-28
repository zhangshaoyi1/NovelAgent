# tools/ — 工具框架

## 职责
为 Agent 提供可调用的工具注册、调用和 MCP 桥接能力。

## 包含文件
| 文件 | 职责 |
|------|------|
| `base.py` | 工具基类（Tool, ToolRegistry, ToolResult） |
| `builtins.py` | 内置工具（项目上下文注入等） |
| `mcp_bridge.py` | MCP 桥接器（MCPBridge - 连接外部 MCP 工具） |

## 依赖规则
- 依赖 base/、client/

## 被依赖
- agents/ (WriterAgent 使用工具)
- workflows/ (agentic_write)