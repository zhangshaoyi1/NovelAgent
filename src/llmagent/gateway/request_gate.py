"""RequestGate：预算预扣 + 缓存查找（M0 缓存为空实现，接口先留）"""

from __future__ import annotations

from .models import AdmitDecision, BudgetSnapshot, ChatRequest, ChatResponse


class RequestGate:
    """请求门禁：预算预扣 + 缓存命中

    M0 中缓存恒返回 None，预算预扣为最小实现。
    """

    def __init__(self) -> None:
        self._cache: dict[str, ChatResponse] = {}

    def admit(self, req: ChatRequest) -> AdmitDecision:
        """预算预扣 + 缓存查找

        1. 缓存查找（M0 恒返回 None）
        2. 预算预扣：不足 → reject(BUDGET)，充足 → ok
        """
        # ① 缓存查找
        cache_key = self._cache_key(req)
        if cache_key in self._cache:
            return AdmitDecision(ok=True, cache_hit=self._cache[cache_key])

        # ② 预算预扣（M0 最小实现：预算为空则放行）
        if not req.budget_ref:
            return AdmitDecision(
                ok=True,
                budget=BudgetSnapshot(ref="unknown", remaining_ratio=1.0),
            )

        # 模拟预算检查（M0 简化：假设预算充足）
        return AdmitDecision(
            ok=True,
            budget=BudgetSnapshot(ref=req.budget_ref, remaining_ratio=0.9),
        )

    @staticmethod
    def _cache_key(req: ChatRequest) -> str:
        """生成缓存键（M0 简单实现）"""
        import hashlib

        raw = "|".join(
            m.get("content", "") for m in req.messages[-3:]
        )
        return hashlib.sha256(raw.encode()).hexdigest()[:16]