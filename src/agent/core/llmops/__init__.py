"""LLMOps 层（Phase 3）—— 可观测 · 成本 · 提示版本 · 评测回归

对外暴露：TraceStore / TraceSpan / get_tracer / set_tracer（调用追踪）、
CostModel（成本基线+告警）、PromptRegistry（提示版本/漂移）、
EvalHarness（评测回归）、TracedLLMClient（可追踪 LLM 包装）。
"""

from __future__ import annotations

from agent.core.llmops.cost import DEFAULT_MODEL_PRICES, TIER_BASELINE_TOKENS_300, CostModel, CostEstimate
from agent.core.llmops.eval_harness import EvalHarness, EvalRun, RegressionIssue
from agent.core.llmops.prompt_version import PromptRegistry
from agent.core.llmops.trace import (
    NullTracer,
    TraceSpan,
    TraceStore,
    get_tracer,
    set_tracer,
)
from agent.core.llmops.traced_llm import TracedLLMClient

__all__ = [
    "TraceStore",
    "TraceSpan",
    "NullTracer",
    "get_tracer",
    "set_tracer",
    "CostModel",
    "CostEstimate",
    "DEFAULT_MODEL_PRICES",
    "TIER_BASELINE_TOKENS_300",
    "PromptRegistry",
    "EvalHarness",
    "EvalRun",
    "RegressionIssue",
    "TracedLLMClient",
]
