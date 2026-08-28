# engine/ — 核心引擎层

## 职责
提供系统运行时的核心控制流与编排能力。

## 包含文件
| 文件 | 职责 |
|------|------|
| `state_machine.py` | 项目生命周期状态管理（State, Event, StateMachine, TRANSITIONS） |
| `agent_loop.py` | Agent 自主决策循环（ReAct, AgentAction, LoopResult） |
| `tool_contracts.py` | 工具契约（Tool, ToolRegistry, ToolResult, @tool, registry 单例；实现见 tools/） |
| `command_router.py` | 命令路由与分发（CommandMeta, CommandRouter） |
| `workflow_orchestrator.py` | 工作流编排（Workflow, Step, WorkflowResult） |
| `workflow_registry.py` | 工作流注册表（WorkflowRegistry, @workflow 装饰器） |
| `events.py` | 进度事件流（ProgressEventBus, next_steps_for, compute_eta_s） |
| `collab.py` | 多 Agent 协作框架（AgentNode, SubtaskDAG, MessageBus） |

## 依赖规则
- 依赖 base/、client/
- 不依赖 story/、quality/、tools/ 等上层业务包

## 被依赖
- workflows/ (所有工作流)
- cli/ (命令路由)
- agents/ (Agent 循环、工具契约)
- infra/ (doctor 诊断状态)
- tools/ (工具契约)