"""Gateway ComplexityRouter：显式 model/provider 指定路由（多模型管理支撑）"""

from __future__ import annotations

from llmagent.gateway.models import (
    BudgetSnapshot,
    ChatRequest,
    HintComplexity,
    ModelCard,
    TaskHint,
)
from llmagent.gateway.router import ComplexityRouter


def _card(provider: str, model: str, cost: float = 1.0, ctx: int = 128000) -> ModelCard:
    return ModelCard(
        provider=provider, model=model,
        cost_per_1k_input_cents=cost, cost_per_1k_output_cents=cost * 2,
        context_window=ctx,
    )


def _cards() -> list[ModelCard]:
    return [_card("openai", "glm-4.7", 0.5, ctx=200000), _card("openai", "glm-4.5-air", 0.1)]


def _req(extra: dict | None = None, complexity=HintComplexity.complex) -> ChatRequest:
    return ChatRequest(
        messages=[{"role": "user", "content": "hi"}],
        hint=TaskHint(complexity=complexity),
        extra=extra or {},
    )


def test_no_extra_keeps_complexity_routing() -> None:
    """未显式指定 → 维持原复杂度分档行为（complex 取上下文最大的第一张卡）"""
    decision = ComplexityRouter().decide(_req(), _cards())
    assert decision.strategy == "complexity"
    assert decision.model == "glm-4.7"


def test_explicit_model_match() -> None:
    decision = ComplexityRouter().decide(_req({"model": "glm-4.5-air"}), _cards())
    assert decision.strategy == "explicit"
    assert decision.model == "glm-4.5-air"
    assert decision.provider == "openai"


def test_explicit_model_unknown_uses_first_card_with_override() -> None:
    """指定了端点可服务的任意模型名（未注册卡片）→ 用第一张可用卡承载该模型名"""
    decision = ComplexityRouter().decide(_req({"model": "dots3-note-prev"}), _cards())
    assert decision.strategy == "explicit"
    assert decision.provider == "openai"
    assert decision.model == "dots3-note-prev"


def test_explicit_provider_and_model() -> None:
    decision = ComplexityRouter().decide(
        _req({"provider": "openai", "model": "glm-4.5-air"}), _cards()
    )
    assert decision.provider == "openai"
    assert decision.model == "glm-4.5-air"


def test_explicit_unknown_provider_not_silently_rerouted() -> None:
    """指定了未注册的 provider → 不静默改道，走原分档逻辑"""
    decision = ComplexityRouter().decide(_req({"provider": "nope"}), _cards())
    assert decision.strategy != "explicit"


def test_explicit_ignores_cost_aware_downgrade() -> None:
    """显式指定不被预算降档覆盖"""
    budget = BudgetSnapshot(ref="r", remaining_ratio=0.1)
    decision = ComplexityRouter().decide(
        _req({"model": "glm-4.7"}), _cards(), budget
    )
    assert decision.strategy == "explicit"
    assert decision.model == "glm-4.7"


def test_explicit_with_empty_registry_falls_through() -> None:
    """无可用卡片 → 走默认卡逻辑（不崩溃）"""
    decision = ComplexityRouter().decide(_req({"model": "any"}), [])
    assert decision.model  # 返回默认内置卡之一
