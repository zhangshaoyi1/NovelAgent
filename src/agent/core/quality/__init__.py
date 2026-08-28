"""质量保障层

职责：提供从写作前到写作后的全链路质量校验体系。
- 一致性校验器：设定冲突/时间线/关系网/金手指/境界校验
- 冲突仲裁服务：新设定与现有设定的冲突检测与仲裁
- 质量校验器：通用+题材层规则校验与自动修订
- 护栏（Guardrails）：内容安全/形式合规/禁用词/占位符检查
- 读者吸引力评分：LLM 驱动的 6 维读者吸引力评分
- 反馈改写器：用户反馈驱动的定向章节重写
- 架构确认门禁：判断项目架构是否已确认

依赖规则：依赖 base、client、story，不依赖 infra/engine 和上层。
"""

from agent.core.quality.conflict_service import (
    ConflictArbiter,
    ConflictReport,
    Conflict,
)
from agent.core.quality.consistency_checker import (
    ConsistencyChecker,
    CheckTrigger,
    ConsistencyReport,
    Severity,
)
from agent.core.quality.quality_checker import QualityChecker, RuleLayer, QualityReport
from agent.core.quality.guardrails import (
    Guardrails,
    GuardrailResult,
    GateMode,
    GuardrailViolation,
    GuardrailViolationError,
    GateReport,
    fullbook_dup_scan,
)
from agent.core.quality.reader_appeal import (
    ReaderAppealScorer,
    ReaderAppealReport,
    build_appeal_summary_lines,
    gate_chapter,
    gate_first_chapters,
    is_pass,
)
from agent.core.quality.feedback_rewriter import FeedbackRewriter, RewriteResult
from agent.core.quality.confirmation import is_architecture_confirmed

__all__ = [
    "ConflictArbiter",
    "ConflictReport",
    "Conflict",
    "ConsistencyChecker",
    "CheckTrigger",
    "ConsistencyReport",
    "QualityChecker",
    "RuleLayer",
    "QualityReport",
    "Guardrails",
    "GuardrailResult",
    "GateMode",
    "GuardrailViolation",
    "GuardrailViolationError",
    "GateReport",
    "fullbook_dup_scan",
    "Severity",
    "ReaderAppealScorer",
    "ReaderAppealReport",
    "build_appeal_summary_lines",
    "gate_chapter",
    "gate_first_chapters",
    "is_pass",
    "FeedbackRewriter",
    "RewriteResult",
    "is_architecture_confirmed",
]