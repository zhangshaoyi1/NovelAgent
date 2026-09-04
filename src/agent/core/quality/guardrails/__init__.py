"""写作门槛与合规子包（guardrails/）

覆盖写作进出的「门槛」类校验：
- 内容安全 / 形式合规护栏（Guardrails 及其值类型、GateReport、门禁模式）
- 配置加载（load_guardrail_config / build_guardrails）
- 全书指纹库持久化（load_fingerprints / save_fingerprints）
- 全书跨章段落去重扫描（fullbook_dup_scan）
- 项目架构确认门禁（is_architecture_confirmed，原 quality/confirmation.py）

依赖规则：仅公用类型与标准库，不依赖 sibling 子包与上层。
"""

from agent.core.quality.guardrails.confirmation import is_architecture_confirmed
from agent.core.quality.guardrails.guardrails import (
    DEFAULT_FINGERPRINT_PATH,
    DEFAULT_GUARDRAIL_CONFIG_PATH,
    GateMode,
    GateReport,
    GuardrailResult,
    GuardrailViolation,
    GuardrailViolationError,
    Guardrails,
    AI_FLAVOR_RULE_ID,
    JUNK_RULE_ID,
    TITLE_RULE_ID,
    DUP_RULE_ID,
    META_LEAK_RULE_ID,
    _DEFAULT_AI_FLAVOR_WORDS,
    build_guardrails,
    fullbook_dup_scan,
    load_fingerprints,
    load_guardrail_config,
    save_fingerprints,
)

__all__ = [
    "Guardrails",
    "GuardrailResult",
    "GateMode",
    "GuardrailViolation",
    "GuardrailViolationError",
    "GateReport",
    "build_guardrails",
    "fullbook_dup_scan",
    "load_guardrail_config",
    "load_fingerprints",
    "save_fingerprints",
    "DEFAULT_GUARDRAIL_CONFIG_PATH",
    "DEFAULT_FINGERPRINT_PATH",
    "is_architecture_confirmed",
    "AI_FLAVOR_RULE_ID",
    "JUNK_RULE_ID",
    "TITLE_RULE_ID",
    "DUP_RULE_ID",
    "META_LEAK_RULE_ID",
]