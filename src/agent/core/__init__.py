"""核心服务层

分层架构（高类聚低耦合）：

- **base/** — 基础基础设施层：异常定义、注册表基类、重试、结构化输出
- **engine/** — 核心引擎层：状态机、Agent 循环、命令路由、工作流编排、事件流
- **story/** — 故事领域模型层：设定管理、伏笔、关系网、章节、高潮曲线、爽点剧本
- **quality/** — 质量保证层：一致性检查、门禁、读者吸引力评分、反馈重写
- **llm/** — LLM 基础设施：模型路由、预算规划
- **registry/** — 扩展机制注册表：技能、题材包
- **infra/** — 基础设施：上下文工程、冲突仲裁、仪表盘聚合
- **event_sourcing/** — 事件溯源：事件总线、存储、恢复
- **rag/** — 检索增强生成：索引、检索、向量存储
- **llmops/** — LLM 运营：成本统计、追踪、评测
- **anti_ai/** — AI 味检测与压制
- **supervisor/** — 长小说监督体系
- **auto_orchestrator/** — 一键自动编排
- **tools/** — 工具框架：工具注册、调用、MCP 桥接

依赖规则：单向依赖，下层不依赖上层，禁止循环依赖。
- base → engine → story/quality/llm/registry/infra → 上层业务（agents/workflows）
"""

from __future__ import annotations

# ── base 层导出 ────────────────────────────────────────────────────
from agent.core.base.exceptions import (
    LLMError,
    FrozenFieldError,
    PreValidationBlocked,
)
from agent.core.base.registry import BaseRegistry
from agent.core.base.retry import retry, RetryError, retry_transport, retry_parse
from agent.core.base.structured_output import (
    pydantic_to_json_schema,
    extract_json,
    StructuredOutputError,
)

# ── engine 层导出 ────────────────────────────────────────────────────
from agent.core.engine.state_machine import State, Event, StateMachine
from agent.core.engine.agent_loop import AgentLoop, AgentAction, LoopResult, LoopStep
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

# ── story 层导出 ────────────────────────────────────────────────────
from agent.core.story.setting_manager import SettingManager
from agent.core.story.foreshadow_manager import (
    ForeshadowManager,
    ForeshadowState,
)
from agent.core.story.relation_manager import RelationManager
from agent.core.story.chapters import (
    strip_frontmatter,
    list_chapter_files,
    take_chapter_files,
    iter_chapter_texts,
    read_chapters_text,
)
from agent.core.story.snapshot_manager import SnapshotManager, ResumeBriefing
from agent.core.story.evidence_chain import EvidenceRef, EvidenceChain
from agent.core.story.tension_curve import (
    TensionCurveManager,
    TensionScore,
    ArcPlan,
    ArcPhase,
)
from agent.core.story.pacing_store import PacingStore, Debt
from agent.core.story.payoff_script import build_payoff_script
from agent.core.story.injected_trope_store import InjectedTropeStore
from agent.core.story.learning_store import LearningStore
from agent.core.story.method_style import load_style_guide, load_method_text
from agent.core.story.meta.worldbuilding_schema import (
    IcebergField,
    IcebergDimension,
    IcebergGroup,
    get_iceberg,
    total_fields,
    summary,
)
from agent.core.story.meta.philosophy import (
    TAGLINE,
    OPENING,
    POSITIONING,
    Pillar,
    CLOSING,
    render_text,
    render_markdown,
    get_philosophy,
)

# ── quality 层导出 ──────────────────────────────────────────────────
from agent.core.quality.conflict_service import (
    Conflict,
    ConflictReport,
    ConflictArbiter,
)
from agent.core.quality.confirmation import (
    is_architecture_confirmed,
)
from agent.core.quality.consistency_checker import (
    CheckTrigger,
    ConsistencyChecker,
    Severity,
)
from agent.core.quality.feedback_rewriter import FeedbackRewriter
from agent.core.quality.guardrails import (
    Guardrails,
    GuardrailResult,
    GateMode,
    GuardrailViolation,
    GuardrailViolationError,
    GateReport,
    build_guardrails,
    save_fingerprints,
    fullbook_dup_scan,
    load_guardrail_config,
    load_fingerprints,
    DEFAULT_GUARDRAIL_CONFIG_PATH,
    DEFAULT_FINGERPRINT_PATH,
)
from agent.core.quality.quality_checker import (
    QualityChecker,
    LLMBackedChecker,
)
from agent.core.quality.reader_appeal import (
    ReaderAppealScorer,
    APPEAL_DIMENSIONS,
    APPEAL_PASS_LINE,
    APPEAL_DIM_FLOOR,
    APPEAL_GATE_PREFIX,
    APPEAL_LABELS,
    gate_chapter,
    build_appeal_summary_lines,
)

# ── llm 层导出 ──────────────────────────────────────────────────────
from agent.core.llm.budget_plan import load_budget_plan
from agent.core.llm.embedding_router import get_embedding_provider
from agent.core.llm.embeddings import (
    EmbeddingProvider,
    OllamaEmbedding,
    OpenAICompatibleEmbedding,
    QwenLocalEmbedding,
)

# ── registry 层导出 ──────────────────────────────────────────────────
from agent.core.registry.skill_registry import (
    get_skill_registry,
    SkillInfo,
    SkillProvider,
    SkillRegistry,
)
from agent.core.registry.genre_pack import (
    GenrePackRegistry,
    GenrePack,
    GenreManifest,
    Trope,
    first_genre,
)
from agent.core.registry.genre_merger import GenreMerger

# ── infra 层导出 ─────────────────────────────────────────────────────
from agent.core.infra.compose_runner import run_compose, resolve_project_dir
from agent.core.infra.context import ContextEngine, ContextItem
from agent.core.infra.context_loader import ContextLoader, LoadedContext
from agent.core.infra.dashboard_aggregator import DashboardAggregator
from agent.core.infra.doctor import Doctor
from agent.core.infra.hook_dispatcher import dispatch_genre_hooks

# ── 延迟导入避免循环依赖 ──────────────────────────────────────────────
def _lazy_import_event_sourcing():
    from agent.core.event_sourcing import EventBus, Event, FileEventStore, RecoveryEngine
    return EventBus, Event, FileEventStore, RecoveryEngine

def _lazy_import_rag():
    from agent.core.rag import (
        Chunk,
        Hit,
        Indexer,
        Retriever,
        VectorStore,
        LocalVectorStore,
        BM25Index,
    )
    return Chunk, Hit, Indexer, Retriever, VectorStore, LocalVectorStore, BM25Index

def _lazy_import_llmops():
    from agent.core.llmops import (
        CostModel,
        EvalHarness,
        PromptRegistry,
        TraceStore,
        TracedLLMClient,
        get_tracer,
        set_tracer,
        build_cost_summary,
    )
    return (CostModel, EvalHarness, PromptRegistry, TraceStore,
            TracedLLMClient, get_tracer, set_tracer, build_cost_summary)

def _lazy_import_anti_ai():
    from agent.core.anti_ai import AILikenessDetector, PostProcessor
    return AILikenessDetector, PostProcessor

def _lazy_import_supervisor():
    from agent.core.supervisor import SupervisorEngine, SupervisionReport
    return SupervisorEngine, SupervisionReport

def _lazy_import_orchestrator():
    from agent.core.auto_orchestrator import AutoPlanner, Decider, Executor, PlanAdjuster
    return AutoPlanner, Decider, Executor, PlanAdjuster

def _lazy_import_tools():
    from agent.core.engine.tool_contracts import Tool, ToolRegistry, ToolResult
    from agent.core.tools.builtins import set_project_context
    return Tool, ToolRegistry, ToolResult, set_project_context

# ── __all__ 按分层顺序导出 ──────────────────────────────────────────────
__all__ = [
    # base
    "LLMError", "FrozenFieldError", "PreValidationBlocked",
    "BaseRegistry",
    "retry", "RetryError", "retry_transport", "retry_parse",
    "pydantic_to_json_schema", "extract_json", "StructuredOutputError",
    # engine
    "State", "Event", "StateMachine",
    "AgentLoop", "AgentAction", "LoopResult", "LoopStep",
    "CommandMeta", "CommandRouter",
    "Workflow", "Step", "WorkflowResult",
    "WorkflowRegistry", "WorkflowType", "workflow", "get_workflow", "list_workflows",
    "ProgressEventBus",
    "AgentNode", "SubtaskDAG", "MessageBus", "CollaborationError",
    # story
    "SettingManager",
    "ForeshadowManager", "ForeshadowState",
    "RelationManager",
    "strip_frontmatter", "list_chapter_files", "take_chapter_files",
    "iter_chapter_texts", "read_chapters_text",
    "SnapshotManager", "ResumeBriefing",
    "EvidenceRef", "EvidenceChain",
    "TensionCurveManager", "TensionScore", "ArcPlan", "ArcPhase",
    "PacingStore", "Debt",
    "build_payoff_script",
    "InjectedTropeStore",
    "LearningStore",
    "load_style_guide", "load_method_text",
    "IcebergField", "IcebergDimension", "IcebergGroup",
    "get_iceberg", "total_fields", "summary",
    "TAGLINE", "OPENING", "POSITIONING", "Pillar", "CLOSING",
    "render_text", "render_markdown", "get_philosophy",
    # quality
    "is_architecture_confirmed",
    "CheckTrigger", "ConsistencyChecker", "Severity",
    "FeedbackRewriter",
    "build_guardrails", "save_fingerprints", "DEFAULT_GUARDRAIL_CONFIG_PATH",
    "QualityChecker", "LLMBackedChecker",
    "ReaderAppealScorer",
    "APPEAL_DIMENSIONS", "APPEAL_PASS_LINE", "APPEAL_DIM_FLOOR",
    "APPEAL_GATE_PREFIX", "APPEAL_LABELS", "gate_chapter", "build_appeal_summary_lines",
    # llm
    "load_budget_plan",
    "get_embedding_provider",
    "EmbeddingProvider",
    "OllamaEmbedding",
    "OpenAICompatibleEmbedding",
    "QwenLocalEmbedding",
    "LLMClient",
    "ModelRouter",
    # registry
    "get_skill_registry", "SkillInfo", "SkillProvider", "SkillRegistry",
    "GenrePackRegistry", "GenrePack", "GenreManifest", "Trope", "first_genre",
    "GenreMerger",
    # infra
    "run_compose", "resolve_project_dir",
    "Conflict", "ConflictReport", "ConflictArbiter",
    "ContextEngine", "ContextItem",
    "ContextLoader", "LoadedContext",
    "DashboardAggregator",
    "Doctor",
    "dispatch_genre_hooks",
    # v1.0 新增模块（延迟导入后导出）
    "EventBus", "Event", "FileEventStore", "RecoveryEngine",
    "Chunk", "Hit", "Indexer", "Retriever", "VectorStore", "LocalVectorStore", "BM25Index",
    "CostModel", "EvalHarness", "PromptRegistry", "TraceStore",
    "TracedLLMClient", "get_tracer", "set_tracer", "build_cost_summary",
    "AILikenessDetector", "PostProcessor",
    "SupervisorEngine", "SupervisionReport",
    "AutoPlanner", "Decider", "Executor", "PlanAdjuster",
    "Tool", "ToolRegistry", "ToolResult", "set_project_context",
]

# 惰性赋值给模块级变量（避免循环导入）
EventBus, Event, FileEventStore, RecoveryEngine = _lazy_import_event_sourcing()
Chunk, Hit, Indexer, Retriever, VectorStore, LocalVectorStore, BM25Index = _lazy_import_rag()
CostModel, EvalHarness, PromptRegistry, TraceStore, TracedLLMClient, get_tracer, set_tracer, build_cost_summary = _lazy_import_llmops()
AILikenessDetector, PostProcessor = _lazy_import_anti_ai()
SupervisorEngine, SupervisionReport = _lazy_import_supervisor()
AutoPlanner, Decider, Executor, PlanAdjuster = _lazy_import_orchestrator()
Tool, ToolRegistry, ToolResult, set_project_context = _lazy_import_tools()
