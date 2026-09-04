"""核心运行时层

提供 Task 运行时、状态机、七统一门面骨架、红线常量。

依赖规则：不依赖 gateway/、tasks/ 等上层模块。

M3 新增模块：
- session.py: 会话管理 + Session 聚合根与三层 Context
- agent.py: AGENT Task 主循环（ReAct）+ Toolset + TurnValidator + Scratchpad
- planner.py: 计划编排 + ExpansionPolicy + StaticDAG + TemplateRetrieval
- memory.py: 记忆写入 + SalienceFilter + MemoryStore
- human.py: 人类介入 + HUMAN Task + 工单 + SLA
"""

# M0 基础
from .task import (
    Executor,
    FailurePolicy,
    TaskKind,
    TaskRun,
    TaskSpec,
    TaskStatus,
    ValidationPolicy,
)

# M1 七统一
from .artifact import ArtifactStore
from .checkpoint import CheckpointManager
from .event_bus import EventBus
from .metrics import Metrics
from .monitor import Monitor
from .redlines import (
    BUDGET_HARD_STOP,
    COMPENSATION_FAIL_ACTION,
    MAX_AGENT_TURNS,
    MAX_REPLAN_DEPTH,
    MAX_RETRY_PER_TRACE,
    POLICY_ERROR_FALLBACK,
)

# M2 完整校验器
from .validator import (
    AllOfValidator,
    AnyOfValidator,
    ChainValidator,
    Composer,
    JsonSchemaValidator,
    ModelRunner,
    NoOpValidator,
    PolicyResolver,
    PureRunner,
    QualityScoreValidator,
    ResultLedger,
    ValidationResult,
    Validator,
    ValidatorRegistry,
    ValidatorRunner,
    WeightedValidator,
    WordCountValidator,
)

# M2 完整失败处理
from .failure import (
    Catcher,
    CaughtError,
    Compensator,
    ErrorClassifier,
    Escalator,
    FailureAction,
    FailureContext,
    FailureHandler,
    FailurePolicy,
    Mutator,
    PolicyResolver as FailurePolicyResolver,
    RedLineGuard,
)

# M2 统治理
from .catalog import (
    Catalog,
    LineageGraph,
    PolicyLoader,
    SchemaGate,
    Versioner,
)

# M3.1 Session 聚合根
from .session import (
    ChatContext,
    ContextBuilder,
    DialogueInterpreter,
    DialogueTurn,
    InputQueue,
    Session,
    SessionContext,
    SessionGate,
    SessionManager,
    SessionState,
    TaskContext,
)

# M3.2 AGENT Task
from .agent import (
    AgentLoopExecutor,
    EchoTool,
    Scratchpad,
    StopDecision,
    StopPolicy,
    Tool,
    ToolCall,
    ToolsetPolicy,
    ToolSpec,
    TurnValidator,
    WriteTool,
)

# M3.3 Planner
from .planner import (
    ExpansionPolicy,
    Plan,
    PlanNode,
    StaticDAG,
    TemplateRetrieval,
)

# M3.4 记忆写入
from .memory import (
    MemoryEntry,
    MemoryManager,
    MemoryStore,
    MemoryWritePolicy,
    SalienceFilter,
    WriteFailureCase,
    WriteHumanCorrection,
    WriteOnSuccess,
)

# M3.5 人类介入
from .human import (
    HUMAN_TASK_SPEC,
    HumanTaskExecutor,
    HumanTicket,
    HumanTicketManager,
    SLAPolicy,
    TimeoutDefaultStrategy,
)


__all__ = [
    # M0
    "Executor", "FailurePolicy", "TaskKind", "TaskRun", "TaskSpec",
    "TaskStatus", "ValidationPolicy",
    # M1
    "ArtifactStore", "CheckpointManager", "EventBus", "Metrics", "Monitor", "BUDGET_HARD_STOP", "COMPENSATION_FAIL_ACTION",
    "MAX_AGENT_TURNS", "MAX_REPLAN_DEPTH", "MAX_RETRY_PER_TRACE",
    "POLICY_ERROR_FALLBACK",
    # M2 校验器
    "AllOfValidator", "AnyOfValidator", "ChainValidator", "Composer",
    "JsonSchemaValidator", "ModelRunner", "NoOpValidator", "PolicyResolver",
    "PureRunner", "QualityScoreValidator", "ResultLedger", "ValidationResult",
    "Validator", "ValidatorRegistry", "ValidatorRunner", "WeightedValidator",
    "WordCountValidator",
    # M2 失败处理
    "Catcher", "CaughtError", "Compensator", "ErrorClassifier", "Escalator",
    "FailureAction", "FailureContext", "FailureHandler",
    "FailurePolicyResolver", "RedLineGuard",
    # M2 统治理
    "Catalog", "LineageGraph", "PolicyLoader", "SchemaGate", "Versioner",
    # M3.1
    "ChatContext", "ContextBuilder", "DialogueInterpreter", "DialogueTurn",
    "InputQueue", "Session", "SessionContext", "SessionGate", "SessionManager",
    "SessionState", "TaskContext",
    # M3.2
    "AgentLoopExecutor", "EchoTool", "Scratchpad", "StopDecision",
    "StopPolicy", "Tool", "ToolCall", "ToolsetPolicy", "ToolSpec",
    "TurnValidator", "WriteTool",
    # M3.3
    "ExpansionPolicy", "Plan", "PlanNode", "StaticDAG", "TemplateRetrieval",
    # M3.4
    "MemoryEntry", "MemoryManager", "MemoryStore", "MemoryWritePolicy",
    "SalienceFilter", "WriteFailureCase", "WriteHumanCorrection",
    "WriteOnSuccess",
    # M3.5
    "HUMAN_TASK_SPEC", "HumanTaskExecutor", "HumanTicket",
    "HumanTicketManager", "SLAPolicy", "TimeoutDefaultStrategy",
]