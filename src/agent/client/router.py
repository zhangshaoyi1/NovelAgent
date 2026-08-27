"""动态模型路由

按请求类型（creative/utility）、成本与历史成功率，从候选模型池动态选择最优模型，
并具备成功率熔断与失败回退链。

设计原则：
- 纯规则 + 统计，零依赖、零网络；候选池 / 成本 / 阈值全部可配置、可注入。
- 与 LLMClient 解耦：只产出 (provider, model_id)，由调用方决定如何构造 Provider。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RouteCandidate:
    """一个候选模型"""

    name: str
    model_id: str
    provider: str = "openai"
    use: str = "both"
    priority: int = 10
    cost_per_1k: float = 0.0
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
    """一次路由决策结果"""

    model_id: str
    provider: str
    candidate_name: str
    source: str
    tripped: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "provider": self.provider,
            "candidate_name": self.candidate_name,
            "source": self.source,
            "tripped": self.tripped,
        }


class ModelRouter:
    """动态模型路由器"""

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
        self._stats: dict[str, dict[str, int]] = {}

    @staticmethod
    def _default_candidates() -> list[RouteCandidate]:
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

    def set_candidates(self, candidates: list[RouteCandidate]) -> None:
        self.candidates = list(candidates)

    def add_candidate(self, c: RouteCandidate) -> None:
        self.candidates.append(c)

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

    def _eligible(self, use: str) -> list[RouteCandidate]:
        return [
            c for c in self.candidates
            if c.active and c.supports(use) and not self.is_tripped(c.model_id)
        ]

    def select(self, use: str, complexity: float | None = None) -> RouteCandidate:
        eligible = self._eligible(use)
        pool = eligible
        tripped = False
        if not pool:
            pool = [c for c in self.candidates if c.active and c.supports(use)]
            tripped = True
        if not pool:
            return RouteCandidate(
                name="default", model_id=self.default_model,
                provider=self.default_provider, use=use,
            )

        if use == "utility":
            pool.sort(key=lambda c: (c.cost_per_1k, c.priority))
        else:
            pool.sort(key=lambda c: (c.priority, c.cost_per_1k))
        chosen = pool[0]
        chosen._tripped = tripped
        return chosen

    def route(self, use: str, complexity: float | None = None) -> RouteDecision:
        c = self.select(use, complexity)
        source = "default" if c.name == "default" else ("fallback" if getattr(c, "_tripped", False) else "selected")
        return RouteDecision(
            model_id=c.model_id, provider=c.provider,
            candidate_name=c.name, source=source,
            tripped=getattr(c, "_tripped", False),
        )

    def report(self) -> dict[str, Any]:
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