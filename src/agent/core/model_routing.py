"""动态模型路由（Phase 4 · Model Supply 强化）

按请求类型（creative / utility）、复杂度、成本与**历史成功率**，从候选模型池动态选择
最优模型，并具备**成功率熔断**与**失败回退链**：

- creative 类请求 → 优先选用「强模型」（priority 小者优先，强模型 priority 小）；
- utility 类请求 → 优先选用「低成本」模型（cost_per_1k 小者优先）；
- 某模型成功率跌破阈值（且样本足够）则**熔断**暂时剔除；
- 主选失败时按 priority 顺序**回退**到下一个可用候选；
- 若全部熔断（极端）则忽略熔断、退回最高 priority 候选，避免完全不可用。

设计原则（与项目一致）：
- 纯规则 + 统计，零依赖、零网络；候选池 / 成本 / 阈值全部可配置、可注入（离线可测）。
- 与 ``LLMClient`` 解耦：只产出 ``(provider, model_id)``，由调用方决定如何构造 Provider。
- 复用 LLMOps 的 ``CostModel`` 单价概念做成本排序（可选）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RouteCandidate:
    """一个候选模型。"""

    name: str
    model_id: str
    provider: str = "openai"
    use: str = "both"            # creative | utility | both
    priority: int = 10           # 越小越强（creative 优先选小）
    cost_per_1k: float = 0.0     # 每 1k token 成本（USD），utility 优先选小
    active: bool = True

    def supports(self, use: str) -> bool:
        return self.use == "both" or self.use == use

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "model_id": self.model_id,
            "provider": self.provider,
            "use": self.use,
            "priority": self.priority,
            "cost_per_1k": self.cost_per_1k,
            "active": self.active,
        }


@dataclass
class RouteDecision:
    """一次路由决策结果。"""

    model_id: str
    provider: str
    candidate_name: str
    source: str                 # selected | fallback | default
    tripped: bool = False       # 是否从熔断池兜底

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "provider": self.provider,
            "candidate_name": self.candidate_name,
            "source": self.source,
            "tripped": self.tripped,
        }


class ModelRouter:
    """动态模型路由器。

    Args:
        candidates: 候选模型列表（默认内置一组 sensible 默认）。
        default_provider / default_model: 无可路由时的兜底。
        circuit_breaker_threshold: 成功率低于此值（且样本 >= min_samples）则熔断。
        min_samples: 触发熔断所需最小调用样本数。
    """

    def __init__(
        self,
        candidates: list[RouteCandidate] | None = None,
        default_provider: str = "openai",
        default_model: str = "glm-5.2",
        circuit_breaker_threshold: float = 0.5,
        min_samples: int = 3,
    ) -> None:
        self.candidates: list[RouteCandidate] = (
            list(candidates) if candidates is not None else self._default_candidates()
        )
        self.default_provider = default_provider
        self.default_model = default_model
        self.circuit_breaker_threshold = circuit_breaker_threshold
        self.min_samples = min_samples
        # 统计：model_id -> {success, fail}
        self._stats: dict[str, dict[str, int]] = {}

    # ---------------------------------------------------------------- 默认候选
    @staticmethod
    def _default_candidates() -> list[RouteCandidate]:
        """一组保守默认（创作强模型 + 校验廉价模型），可按项目 .env 覆盖。"""
        return [
            RouteCandidate(
                name="creative-strong", model_id="glm-5.2", provider="openai",
                use="creative", priority=1, cost_per_1k=0.012,
            ),
            RouteCandidate(
                name="creative-fallback", model_id="glm-4.7", provider="openai",
                use="creative", priority=3, cost_per_1k=0.006,
            ),
            RouteCandidate(
                name="utility-cheap", model_id="glm-4-flash", provider="openai",
                use="utility", priority=2, cost_per_1k=0.001,
            ),
            RouteCandidate(
                name="utility-local", model_id="qwen2.5", provider="ollama",
                use="utility", priority=4, cost_per_1k=0.0,
            ),
        ]

    # ---------------------------------------------------------------- 配置
    def set_candidates(self, candidates: list[RouteCandidate]) -> None:
        self.candidates = list(candidates)

    def add_candidate(self, c: RouteCandidate) -> None:
        self.candidates.append(c)

    # ---------------------------------------------------------------- 统计
    def record_success(self, model_id: str, latency_ms: float | None = None,
                       cost: float | None = None) -> None:
        s = self._stats.setdefault(model_id, {"success": 0, "fail": 0})
        s["success"] += 1

    def record_failure(self, model_id: str) -> None:
        s = self._stats.setdefault(model_id, {"success": 0, "fail": 0})
        s["fail"] += 1

    def success_rate(self, model_id: str) -> float:
        s = self._stats.get(model_id)
        if not s:
            return 1.0
        total = s["success"] + s["fail"]
        if total == 0:
            return 1.0
        return s["success"] / total

    def is_tripped(self, model_id: str) -> bool:
        s = self._stats.get(model_id)
        if not s:
            return False
        total = s["success"] + s["fail"]
        if total < self.min_samples:
            return False
        return self.success_rate(model_id) < self.circuit_breaker_threshold

    # ---------------------------------------------------------------- 路由
    def _eligible(self, use: str) -> list[RouteCandidate]:
        return [
            c for c in self.candidates
            if c.active and c.supports(use) and not self.is_tripped(c.model_id)
        ]

    def select(self, use: str, complexity: float | None = None) -> RouteCandidate:
        """选出最优候选（不返回 None；极端情况下兜底到最高 priority 候选）。"""
        eligible = self._eligible(use)
        pool = eligible
        tripped = False
        if not pool:
            # 全部熔断 / 无可用：忽略熔断，退回全部 active 候选，避免完全不可用
            pool = [c for c in self.candidates if c.active and c.supports(use)]
            tripped = True
        if not pool:
            # 连候选都没有：返回默认占位候选
            return RouteCandidate(
                name="default", model_id=self.default_model,
                provider=self.default_provider, use=use,
            )

        if use == "utility":
            # 低成本优先，其次 priority 小
            pool.sort(key=lambda c: (c.cost_per_1k, c.priority))
        else:
            # creative / both：强模型优先（priority 小），其次成本
            pool.sort(key=lambda c: (c.priority, c.cost_per_1k))
        chosen = pool[0]
        chosen._tripped = tripped  # type: ignore[attr-defined]
        return chosen

    def route(self, use: str, complexity: float | None = None) -> RouteDecision:
        """产出 ``(provider, model_id)`` 决策。"""
        c = self.select(use, complexity)
        source = "default" if c.name == "default" else ("fallback" if getattr(c, "_tripped", False) else "selected")
        return RouteDecision(
            model_id=c.model_id, provider=c.provider,
            candidate_name=c.name, source=source,
            tripped=getattr(c, "_tripped", False),
        )

    def report(self) -> dict[str, Any]:
        """返回路由表 + 健康快照（CLI / 看板用）。"""
        return {
            "candidates": [c.to_dict() for c in self.candidates],
            "stats": {
                m: {
                    "success": s["success"], "fail": s["fail"],
                    "success_rate": round(self.success_rate(m), 3),
                    "tripped": self.is_tripped(m),
                }
                for m, s in self._stats.items()
            },
            "circuit_breaker_threshold": self.circuit_breaker_threshold,
            "min_samples": self.min_samples,
        }
