"""LLMOps · 成本模型与档位基线（Phase 3）

把设计文档 §1.4 的「Token 档位策略」落地为**成本基线 + 告警**：

- 档位（默认 均衡档）：经济 / 均衡 / 质量，对应不同的整本 token 消耗基线
  （以 300 章为基准，按比例缩放）。
- 各模型单价可配置（默认给一组占位单价，真实单价在配置中覆盖）。
- 提供 ``estimate_*`` 估算与 ``alert_if_over`` 成本告警（不拦截，只提示）。

纯离线、零依赖，便于单元测试与 CLI 成本看板。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# 档位策略（§1.4）：以 300 章为基准的整本 token 消耗区间（输入+输出混合）。
TIER_BASELINE_TOKENS_300: dict[str, tuple[float, float]] = {
    "economy": (6_000_000, 9_000_000),     # 经济档
    "balanced": (10_000_000, 16_000_000),  # 均衡档（默认）
    "quality": (18_000_000, 30_000_000),   # 质量档
}

# 单章粗估（§1.4）：25k–35k tokens。
CHAPTER_TOKENS: tuple[float, float] = (25_000, 35_000)

# 模型单价（每 1M token 的 USD 估算，占位；真实值在配置中覆盖）。
DEFAULT_MODEL_PRICES: dict[str, dict[str, float]] = {
    "creative-strong": {"in": 10.0, "out": 30.0},
    "creative-light": {"in": 1.0, "out": 2.0},
    "utility": {"in": 0.5, "out": 1.5},
}


@dataclass
class CostEstimate:
    tier: str
    chapters: int
    tokens_low: float
    tokens_high: float
    cost_low_usd: float
    cost_high_usd: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "chapters": self.chapters,
            "tokens_low": self.tokens_low,
            "tokens_high": self.tokens_high,
            "cost_low_usd": round(self.cost_low_usd, 2),
            "cost_high_usd": round(self.cost_high_usd, 2),
        }


class CostModel:
    """成本模型。

    Args:
        model_prices: 模型名 -> {"in": 单价, "out": 单价}（每 1M token USD）。
        default_model: 估算时默认采用的模型（取 in/out 均价）。
    """

    def __init__(
        self,
        model_prices: dict[str, dict[str, float]] | None = None,
        default_model: str = "creative-strong",
    ) -> None:
        self.prices = dict(model_prices or DEFAULT_MODEL_PRICES)
        self.default_model = default_model if default_model in self.prices else (
            next(iter(self.prices))
        )

    # ---------------------------------------------------------------- 基线
    def baseline_tokens(self, tier: str, chapters: int) -> tuple[float, float]:
        """返回该档位、该章节数的整本 token 消耗区间。"""
        low300, high300 = TIER_BASELINE_TOKENS_300.get(
            tier, TIER_BASELINE_TOKENS_300["balanced"]
        )
        scale = chapters / 300.0
        return low300 * scale, high300 * scale

    def _avg_price(self, model: str | None) -> float:
        m = model or self.default_model
        p = self.prices.get(m, {"in": 0.0, "out": 0.0})
        return (p.get("in", 0.0) + p.get("out", 0.0)) / 2.0

    # ---------------------------------------------------------------- 估算
    def estimate_book(self, tier: str, chapters: int, model: str | None = None) -> CostEstimate:
        low, high = self.baseline_tokens(tier, chapters)
        price = self._avg_price(model)
        return CostEstimate(
            tier=tier,
            chapters=chapters,
            tokens_low=low,
            tokens_high=high,
            cost_low_usd=low / 1_000_000 * price,
            cost_high_usd=high / 1_000_000 * price,
        )

    def estimate_chapter(self, tier: str = "balanced", model: str | None = None) -> CostEstimate:
        low, high = CHAPTER_TOKENS
        price = self._avg_price(model)
        return CostEstimate(
            tier=tier,
            chapters=1,
            tokens_low=low,
            tokens_high=high,
            cost_low_usd=low / 1_000_000 * price,
            cost_high_usd=high / 1_000_000 * price,
        )

    # ---------------------------------------------------------------- 告警
    def alert_if_over(
        self, tokens_used: float, tier: str, chapters_planned: int
    ) -> str | None:
        """若已用 token 超过该档位基线上限，返回告警文案；否则 None。"""
        _, high = self.baseline_tokens(tier, chapters_planned)
        if tokens_used > high:
            over = tokens_used - high
            return (
                f"⚠ 成本告警：已用 {tokens_used/1_000_000:.2f}M tokens，"
                f"超过 {tier} 档 {chapters_planned} 章基线上限 "
                f"{high/1_000_000:.2f}M（超出 {over/1_000_000:.2f}M）"
            )
        return None
