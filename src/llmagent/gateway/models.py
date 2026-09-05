"""Gateway 数据模型：ChatRequest / ChatResponse / 路由 / 预算 等"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class HintComplexity(str, Enum):
    simple = "simple"
    complex = "complex"


class ErrorClass(str, Enum):
    """归一化错误分类（对应 ErrorClassifier 8 类）"""

    BUDGET = "BUDGET"
    CONTEXT_OVERFLOW = "CONTEXT_OVERFLOW"
    RATE_LIMIT = "RATE_LIMIT"
    TRANSIENT = "TRANSIENT"
    DETERMINISTIC = "DETERMINISTIC"
    CONTENT_FILTER = "CONTENT_FILTER"
    SEMANTIC = "SEMANTIC"
    TIMED_OUT = "TIMED_OUT"
    UNKNOWN = "UNKNOWN"


@dataclass
class TaskHint:
    complexity: HintComplexity = HintComplexity.complex
    quality_critical: bool = True
    max_tokens: int | None = None
    temperature: float | None = None


@dataclass
class BudgetSnapshot:
    ref: str
    remaining_ratio: float = 1.0
    total_cents: float = 0.0
    used_cents: float = 0.0


@dataclass
class ModelCard:
    provider: str
    model: str
    cost_per_1k_input_cents: float = 0.0
    cost_per_1k_output_cents: float = 0.0
    context_window: int = 4096


@dataclass
class RouteDecision:
    provider: str
    model: str
    card: ModelCard
    strategy: str = "default"  # complexity / cost_aware / fallback
    budget: BudgetSnapshot | None = None


@dataclass
class ChatRequest:
    messages: list[dict[str, str]]
    hint: TaskHint = field(default_factory=TaskHint)
    budget_ref: str = ""
    run_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class PackedRequest:
    messages: list[dict[str, str]]
    estimated_input_tokens: int = 0
    context_fingerprint: str = ""
    route: RouteDecision | None = None
    # 来自 req.hint.temperature 的按次采样温度（None=由 Provider 用自身默认值）
    temperature: float | None = None
    # 来自 req.extra["enable_thinking"] 的按次思考开关（None=回退 Provider 配置）
    enable_thinking: bool | None = None


@dataclass
class RawResponse:
    text: str
    provider: str = ""
    model: str = ""
    usage_input: int = 0
    usage_output: int = 0
    elapsed_ms: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatResponse:
    text: str
    provider: str = ""
    model: str = ""
    context_fingerprint: str = ""
    usage_input: int = 0
    usage_output: int = 0
    elapsed_ms: float = 0.0
    warnings: list[str] = field(default_factory=list)


@dataclass
class AdmitDecision:
    ok: bool = False
    reject_reason: str = ""
    budget: BudgetSnapshot | None = None
    cache_hit: ChatResponse | None = None