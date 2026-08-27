"""核心服务层

包含状态机、命令路由、工作流编排、设定集管理、上下文加载、
一致性校验、质量校验、伏笔管理、关系网管理、快照、LLM 抽象。

Registry 基础设施（DeepSeek Harness 风格）：
- `registry.BaseRegistry` — 通用注册表基类
- `workflow_registry.WorkflowRegistry` + `@workflow` — 工作流注册表
- `skill_registry.SkillRegistry` — 统一 Skill 注册表
- `genre_pack.GenrePackRegistry` — 题材包注册表

v1.0 新增模块：
- `events/` — 事件系统 Event Sourcing
- `retry.py` — 统一重试机制
- `anti_ai/` — AI 味检测与压制
- `tension_curve.py` — 高潮曲线管理
- `supervisor/` — 长小说监督体系
- `auto_orchestrator/` — 一键完成自动编排
"""

from agent.core.registry import BaseRegistry
from agent.core.workflow_registry import workflow, get_workflow, list_workflows
from agent.core.skill_registry import get_skill_registry, SkillInfo, SkillProvider, SkillRegistry
from agent.core.genre_pack import GenrePackRegistry, GenrePack, GenreManifest, Trope

# v1.0 新增模块（延迟导入，避免循环依赖）
def _lazy_import_events():
    from agent.core.event_sourcing import EventBus, Event, FileEventStore, RecoveryEngine
    return EventBus, Event, FileEventStore, RecoveryEngine

def _lazy_import_retry():
    from agent.core.retry import retry, RetryError, retry_transport, retry_parse
    return retry, RetryError, retry_transport, retry_parse

def _lazy_import_anti_ai():
    from agent.core.anti_ai import AILikenessDetector, PostProcessor
    return AILikenessDetector, PostProcessor

def _lazy_import_tension():
    from agent.core.tension_curve import TensionCurveManager
    return TensionCurveManager

def _lazy_import_supervisor():
    from agent.core.supervisor import SupervisorEngine, SupervisionReport
    return SupervisorEngine, SupervisionReport

def _lazy_import_orchestrator():
    from agent.core.auto_orchestrator import AutoPlanner, Decider, Executor, PlanAdjuster
    return AutoPlanner, Decider, Executor, PlanAdjuster

__all__ = [
    # registry base
    "BaseRegistry",
    # workflow
    "workflow",
    "get_workflow",
    "list_workflows",
    # skill
    "get_skill_registry",
    "SkillInfo",
    "SkillProvider",
    "SkillRegistry",
    # genre pack
    "GenrePackRegistry",
    "GenrePack",
    "GenreManifest",
    "Trope",
    # v1.0 新增
    "EventBus", "Event", "FileEventStore", "RecoveryEngine",
    "retry", "RetryError", "retry_transport", "retry_parse",
    "AILikenessDetector", "PostProcessor",
    "TensionCurveManager",
    "SupervisorEngine", "SupervisionReport",
    "AutoPlanner", "Decider", "Executor", "PlanAdjuster",
]

