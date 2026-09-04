"""质量保障层

职责：提供从写作前到写作后的全链路质量校验体系，按关注点拆分为子包：
- guardrails/：写作门槛与合规（内容安全/形式合规护栏、配置、指纹、全书去重扫描、架构确认门禁）
- consistency/：设定一致性校验与冲突仲裁
- scoring/：通用+题材层质量评分与读者吸引力评分
- rewrite/：反馈驱动的章节改写

依赖规则：依赖 base、client、story，不依赖 infra/engine 和上层。
"""

from agent.core.quality.guardrails import (
    DEFAULT_FINGERPRINT_PATH,
    DEFAULT_GUARDRAIL_CONFIG_PATH,
    GateMode,
    GateReport,
    GuardrailResult,
    GuardrailViolation,
    GuardrailViolationError,
    Guardrails,
    build_guardrails,
    fullbook_dup_scan,
    is_architecture_confirmed,
    load_fingerprints,
    load_guardrail_config,
    save_fingerprints,
)
from agent.core.quality.consistency import (
    CheckTrigger,
    Conflict,
    ConflictArbiter,
    ConflictReport,
    ConsistencyChecker,
    ConsistencyReport,
    Severity,
)
from agent.core.quality.scoring import (
    QualityChecker,
    QualityReport,
    ReaderAppealReport,
    ReaderAppealScorer,
    RuleLayer,
    build_appeal_summary_lines,
    gate_chapter,
    gate_first_chapters,
    is_pass,
)
from agent.core.quality.rewrite import FeedbackRewriter, RewriteResult

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