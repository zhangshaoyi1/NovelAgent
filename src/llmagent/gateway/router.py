"""Router：按 Task 意图 + 全局视角选模型"""

from __future__ import annotations

import json
import statistics
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from .models import (
    BudgetSnapshot,
    ChatRequest,
    HintComplexity,
    ModelCard,
    RouteDecision,
)

if TYPE_CHECKING:  # pragma: no cover
    from .models import ChatResponse


class HintCalibrator:
    """O4：按历史真实用量校准 Task 自评复杂度。

    Task 手工声明的 ``hint.complexity`` 可能系统性低估（自评 simple 但实际
    产出数千 token）。校准器记录每次调用 (task 标签, 声明档位, 实际输出 token)，
    当某标签声明 simple 但历史中位输出超过 `SIMPLE_BUMP_TOKENS`（且样本数
    ≥ MIN_SAMPLES）时，路由时把该标签的 effective 复杂度抬升为 complex。

    持久化：`persist_path` 给定时按 JSON 落盘，跨进程累积；不给定则仅内存。
    """

    MIN_SAMPLES = 3
    SIMPLE_BUMP_TOKENS = 2000

    def __init__(self, persist_path: str | Path | None = None) -> None:
        self._persist_path = Path(persist_path) if persist_path else None
        self._lock = threading.Lock()
        # task 标签 -> {"simple": [output_tokens, ...], "complex": [...]}
        self._history: dict[str, dict[str, list[int]]] = {}
        if self._persist_path and self._persist_path.exists():
            try:
                self._history = json.loads(
                    self._persist_path.read_text(encoding="utf-8")
                )
            except Exception:  # noqa: BLE001 - 校准数据损坏时归零，不阻断路由
                self._history = {}

    def record(
        self,
        task_label: str,
        declared: HintComplexity,
        output_tokens: int,
    ) -> None:
        """记录一次真实用量（在 Gateway.chat 成功返回后调用）。"""
        if not task_label or output_tokens <= 0:
            return
        key = declared.value if hasattr(declared, "value") else str(declared)
        with self._lock:
            bucket = self._history.setdefault(task_label, {}).setdefault(key, [])
            bucket.append(int(output_tokens))
            # 每标签每档位只保留最近 50 条，防无限膨胀
            del bucket[:-50]
            if self._persist_path:
                try:
                    self._persist_path.parent.mkdir(parents=True, exist_ok=True)
                    self._persist_path.write_text(
                        json.dumps(self._history, ensure_ascii=False),
                        encoding="utf-8",
                    )
                except Exception:  # noqa: BLE001 - 落盘失败不影响本次调用
                    pass

    def suggest(
        self,
        task_label: str,
        declared: HintComplexity,
    ) -> HintComplexity:
        """返回该标签的 effective 复杂度（simple 且历史中位超阈值 → complex）。"""
        if declared is not HintComplexity.simple or not task_label:
            return declared
        with self._lock:
            samples = list(self._history.get(task_label, {}).get("simple", []))
        if len(samples) < self.MIN_SAMPLES:
            return declared
        if statistics.median(samples) > self.SIMPLE_BUMP_TOKENS:
            return HintComplexity.complex
        return declared


class CascadeRoute:
    """O5：Cascade 先小后大（含 Task 声明的 verify 回调）。

    策略协议实现：普通决策直接复用 ``inner``（默认 ComplexityRouter）；
    当请求声明 cascade（``req.extra["cascade"]`` 为真）且提供了校验回调
    （``req.extra["verify"]``）时，先选便宜模型，由调用方（Gateway.chat）
    在 verify 失败后经 :meth:`escalate` 升级到最强模型重试。
    """

    def __init__(self, inner: RoutePolicy | None = None) -> None:
        self._inner = inner or ComplexityRouter()

    def decide(
        self,
        req: ChatRequest,
        available: list[ModelCard],
        budget: BudgetSnapshot | None,
    ) -> RouteDecision:
        decision = self._inner.decide(req, available, budget)
        extra = req.extra or {}
        if not extra.get("cascade") or not callable(extra.get("verify")):
            return decision
        # cascade 首跳：选最便宜卡（显式指定 model 时不降级，尊重调用方意图）
        if decision.strategy == "explicit" or not available:
            return decision
        cheap = min(available, key=lambda c: c.cost_per_1k_input_cents)
        if cheap.model == decision.model:
            return decision
        return RouteDecision(
            provider=cheap.provider,
            model=cheap.model,
            card=cheap,
            strategy="cascade_first",
            budget=budget,
        )

    @staticmethod
    def escalate(
        available: list[ModelCard],
        current: ModelCard,
        budget: BudgetSnapshot | None,
    ) -> RouteDecision | None:
        """verify 失败后的升级跳：选比当前最强（上下文窗口最大）的卡。"""
        stronger = [
            c for c in available
            if (c.context_window, c.cost_per_1k_input_cents)
            > (current.context_window, current.cost_per_1k_input_cents)
        ]
        if not stronger:
            return None
        best = max(
            stronger, key=lambda c: (c.context_window, c.cost_per_1k_input_cents)
        )
        return RouteDecision(
            provider=best.provider,
            model=best.model,
            card=best,
            strategy="cascade_escalate",
            budget=budget,
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

    def __init__(self, calibrator: "HintCalibrator | None" = None) -> None:
        # O4：历史用量校准器（None = 不校准，完全按 Task 自评 hint）
        self._calibrator = calibrator
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

        # 显式指定优先：调用方经 req.extra["provider"] / req.extra["model"]
        # （chat_creative / chat_utility / chat_structured 的 model= 参数）指定
        # 目标模型时直接命中，跳过复杂度分档与预算降档。
        extra = req.extra or {}
        explicit_provider = str(extra.get("provider") or "").strip()
        explicit_model = str(extra.get("model") or "").strip()
        if explicit_provider or explicit_model:
            selected = self._match_explicit(
                cards, explicit_provider, explicit_model
            )
            if selected is not None:
                return RouteDecision(
                    provider=selected.provider,
                    model=explicit_model or selected.model,
                    card=selected,
                    strategy="explicit",
                    budget=budget,
                )

        # 按复杂度分档（O4：先经历史用量校准）
        effective = req.hint.complexity
        strategy = "complexity"
        if self._calibrator is not None:
            label = str(extra.get("task") or "")
            calibrated = self._calibrator.suggest(label, req.hint.complexity)
            if calibrated is not req.hint.complexity:
                effective = calibrated
                strategy = "complexity(calibrated)"
        if effective == HintComplexity.simple:
            # 小模型：找最便宜的可用模型
            selected = min(cards, key=lambda c: c.cost_per_1k_input_cents)
        else:
            # 大模型：找能力最强的（按上下文窗口 + 成本综合）
            selected = max(cards, key=lambda c: (c.context_window, c.cost_per_1k_input_cents))

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

    def record(self, req: "ChatRequest", resp: "ChatResponse") -> None:
        """Gateway.chat 成功后回写真实用量，供 HintCalibrator 学习（O4）。"""
        if self._calibrator is None:
            return
        extra = req.extra or {}
        label = str(extra.get("task") or "")
        try:
            self._calibrator.record(label, req.hint.complexity, int(resp.usage_output))
        except Exception:  # noqa: BLE001 - 校准记录失败不影响主链路
            pass

    @staticmethod
    def _match_explicit(
        cards: list[ModelCard],
        explicit_provider: str,
        explicit_model: str,
    ) -> ModelCard | None:
        """按显式 provider/model 匹配模型卡。

        匹配顺序：provider+model 精确命中 > 仅 model 命中 > 仅 provider 命中。
        仅指定 model 未命中时（OpenAI 兼容端点通常可服务任意 model 名）返回
        第一张可用卡，由调用方以 explicit_model 覆盖其模型名。
        指定了 provider 但无任何命中时返回 None（走原分档逻辑，不静默改道）。
        """
        if explicit_provider and explicit_model:
            for c in cards:
                if c.provider == explicit_provider and c.model == explicit_model:
                    return c
        if explicit_model:
            for c in cards:
                if c.model == explicit_model:
                    return c
            if not explicit_provider and cards:
                return cards[0]
        if explicit_provider:
            for c in cards:
                if c.provider == explicit_provider:
                    return c
        return None