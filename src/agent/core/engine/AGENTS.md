# AGENTS.md - core/engine/ 核心引擎层

## 职责

提供系统运行时的核心控制流与编排能力。

## 核心模块

| 文件 | 导出 | 作用 |
|------|------|------|
| `state_machine.py` | `State`, `Event`, `StateMachine` | 状态机（项目生命周期状态管理） |
| `agent_loop.py` | `AgentLoop`, `AgentAction`, `LoopResult`, `LoopStep` | Agent 自主决策循环（ReAct） |
| `tool_contracts.py` | `Tool`, `ToolResult`, `ToolRegistry`, `registry`, `tool` | 工具契约（接口与实现分离） |
| `command_router.py` | `CommandMeta`, `CommandRouter` | 命令路由与分发 |
| `workflow_orchestrator.py` | `WorkflowOrchestrator`, `Workflow`, `Step`, `WorkflowResult` | 工作流编排 |
| `workflow_registry.py` | `WorkflowRegistry`, `workflow`, `get_workflow`, `list_workflows`, `WorkflowType` | 工作流注册与发现 |
| `events.py` | `ProgressEventBus` | 进度事件流 |
| `collab.py` | `AgentNode`, `SubtaskDAG`, `MessageBus`, `CollaborationError` | 多 Agent 协作框架 |

## 依赖规则

- 依赖 `base`、`client`
- 不依赖 `story`/`quality`/`tools` 等上层业务包
- 工具契约（`Tool`/`ToolRegistry`/`@tool`）定义在 engine 层，实现见 `core/tools/`