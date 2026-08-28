"""基础设施层

职责：提供跨领域的通用基础设施，供所有上层包使用。
- 上下文工程：重要性加权、压缩、预算裁剪、Prompt Caching
- 上下文加载器：按场景智能加载设定，控制 token 用量
- 冲突仲裁服务：设定冲突检测与仲裁
- 一键写书编排器：compose 流程编排
- 仪表盘聚合器：写作进度数据聚合
- Hook 分发器：写作流程中的 Hook 机制
- 诊断器：项目问题诊断

依赖规则：依赖 base、client，可依赖 story 的部分模块。
"""

from agent.core.infra.context import ContextEngine, ContextItem
from agent.core.infra.context_loader import ContextLoader, LoadedContext
from agent.core.infra.conflict_service import ConflictArbiter, ConflictReport, Conflict
from agent.core.infra.compose_runner import run_compose, resolve_project_dir
from agent.core.infra.dashboard_aggregator import DashboardAggregator
from agent.core.infra.hook_dispatcher import dispatch_genre_hooks
from agent.core.infra.doctor import Doctor

__all__ = [
    "ContextEngine",
    "ContextItem",
    "ContextLoader",
    "LoadedContext",
    "ConflictArbiter",
    "ConflictReport",
    "Conflict",
    "run_compose",
    "resolve_project_dir",
    "DashboardAggregator",
    "dispatch_genre_hooks",
    "Doctor",
]