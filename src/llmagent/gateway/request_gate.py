"""RequestGate：预算预扣（M0 缓存为空实现，接口先留）

2026-09-08（HA-Eval L1）：**删除**原此处的第二套缓存实现（``self._cache`` /
``_cache_key``）。它从未被写入（全仓库无任何 store 调用点），但键规则与
``SemanticCache`` 完全分叉——完整 content 拼接 vs 末 200 字符采样、无 TTL、
无容量上限、无 ``quality_critical`` 豁免。留着等于给"缓存策略无单一事实来源"
留了活证据：只要有人补上写入点，就是第二个定时炸弹。

缓存裁决统一收口到 ``llmagent.gateway.cache_policy.decide``，
由 ``SemanticCache`` 执行（见 ``chat.Gateway.chat`` 第 ① 段之后）。
"""

from __future__ import annotations

from .models import AdmitDecision, BudgetSnapshot, ChatRequest


class RequestGate:
    """请求门禁：预算预扣

    M0 中缓存恒返回 None，预算预扣为最小实现。
    """

    def admit(self, req: ChatRequest) -> AdmitDecision:
        """预算预扣

        1. 预算为空（``budget_ref`` 未设置）→ 放行；
        2. 预算检查（M0 简化：假设预算充足）。

        Note:
            ``AdmitDecision.cache_hit`` 恒为 ``None``——缓存已统一由
            ``SemanticCache`` 负责，本层不再参与。
        """
        # ① 预算预扣（M0 最小实现：预算为空则放行）
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
