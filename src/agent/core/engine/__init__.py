"""核心引擎层

职责：提供系统运行时的核心控制流与编排能力。
- 状态机：项目生命周期状态管理
- Agent 自主决策循环（ReAct）
- 工具契约：Tool / ToolRegistry / @tool（实现见 tools/，engine 不依赖实现包）
- 命令路由与分发
- 工作流编排与注册
- 进度事件流
- 多 Agent 协作框架

依赖规则：依赖 base、client，不依赖 story/quality/tools 等上层业务包。
"""

from agent.core.engine.state_machine import State, Event, StateMachine
from agent.core.engine.agent_loop import AgentLoop, AgentAction, LoopResult, LoopStep
from agent.core.engine.tool_contracts import Tool, ToolResult, ToolRegistry, registry, tool
from agent.core.engine.command_router import CommandMeta, CommandRouter
from agent.core.engine.workflow_orchestrator import Workflow, Step, WorkflowResult
from agent.core.engine.workflow_registry import (
    WorkflowRegistry,
    workflow,
    get_workflow,
    list_workflows,
    WorkflowType,
)
from agent.core.engine.events import ProgressEventBus
from agent.core.engine.collab import AgentNode, SubtaskDAG, MessageBus, CollaborationError

__all__ = [
    "State",
    "Event",
    "StateMachine",
    "AgentLoop",
    "AgentAction",
    "LoopResult",
    "LoopStep",
    "Tool",
    "ToolResult",
    "ToolRegistry",
    "registry",
    "tool",
    "CommandMeta",
    "CommandRouter",
    "Workflow",
    "Step",
    "WorkflowResult",
    "WorkflowRegistry",
    "WorkflowType",
    "workflow",
    "get_workflow",
    "list_workflows",
    "ProgressEventBus",
    "AgentNode",
    "SubtaskDAG",
    "MessageBus",
    "CollaborationError",
]