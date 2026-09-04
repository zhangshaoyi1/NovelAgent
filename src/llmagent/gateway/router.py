"""Router：按 Task 意图 + 全局视角选模型"""

from __future__ import annotations

from typing import Protocol

from .models import (
    BudgetSnapshot,
    ChatRequest,
    HintComplexity,
    ModelCard,
    RouteDecision,
)


class RoutePolicy(Protocol):
    """路由策略协议"""

    def decide(
        self,
        req: ChatRequest,
        available: list[ModelCard],
        budget: BudgetSnapshot | None,
    ) -> RouteDecision:
        ...


class ComplexityRouter:
    """内置复杂度路由：simple → 小模型 / complex → 大模型

    CostAware 兜底：budget.remaining_ratio < 0.2 → 强制降一档。
    """

    def __init__(self) -> None:
        # 内置模型卡（M0 硬编码，后续从 ProviderRegistry 获取）
        self._default_cards: list[ModelCard] = [
            ModelCard(
                provider="openai",
                model="gpt-4o",
                cost_per_1k_input_cents=0.5,
                cost_per_1k_output_cents=1.5,
                context_window=128000,
            ),
            ModelCard(
                provider="openai",
                model="gpt-4o-mini",
                cost_per_1k_input_cents=0.015,
                cost_per_1k_output_cents=0.06,
                context_window=128000,
            ),
            ModelCard(
                provider="qwen",
                model="qwen-max",
                cost_per_1k_input_cents=0.2,
                cost_per_1k_output_cents=0.6,
                context_window=32000,
            ),
            ModelCard(
                provider="qwen",
                model="qwen-plus",
                cost_per_1k_input_cents=0.08,
                cost_per_1k_output_cents=0.24,
                context_window=32000,
            ),
        ]

    def decide(
        self,
        req: ChatRequest,
        available: list[ModelCard] | None = None,
        budget: BudgetSnapshot | None = None,
    ) -> RouteDecision:
        cards = available or self._default_cards

        # 按复杂度分档
        if req.hint.complexity == HintComplexity.simple:
            # 小模型：找最便宜的可用模型
            selected = min(cards, key=lambda c: c.cost_per_1k_input_cents)
            strategy = "complexity"
        else:
            # 大模型：找能力最强的（按上下文窗口 + 成本综合）
            selected = max(cards, key=lambda c: (c.context_window, -c.cost_per_1k_input_cents))
            strategy = "complexity"

        # CostAware 兜底：预算不足时降档
        if budget and budget.remaining_ratio < 0.2:
            cheaper = sorted(cards, key=lambda c: c.cost_per_1k_input_cents)
            if cheaper[0].provider != selected.provider or cheaper[0].model != selected.model:
                selected = cheaper[0]
                strategy = "cost_aware"

        return RouteDecision(
            provider=selected.provider,
            model=selected.model,
            card=selected,
            strategy=strategy,
            budget=budget,
        )