"""批末停批裁决统一出口（BatchOutcome，20261003 复盘·类 2/横向契约第二批）

背景
----
批末停批此前有**四条平行通道**：``result.tripped``（预算熔断）、
``_gate_escalation_reason``（门禁失明/告警留章）、``_rolling_escalation_reason``
（滚动体检）、``result.blocked``（规划异常）——各自置位、各自 reason 字符串、
run() 收敛处按 if-elif 人肉排优先级。2026-10-03 实弹《凡尘炼废》新增第五个
停批源（规划闸门）时，缝直接以 UnboundLocalError / 吞噬两种形态爆开
（复盘单 20261003 类 1/类 2）。

设计
----
- :class:`StopKind`：停批来源枚举——**新增停批源 = 加一个枚举成员 + 在置位点调
  :meth:`BatchOutcome.register`，不再新增平行标志位/私有 reason 字符串**。
- :class:`BatchOutcome`：存储 + 收敛。唯一收敛规则是
  :data:`STOP_PRECEDENCE`（数据表，非 if-elif）：多源并发时取优先级最高者，
  同级取先登记者。遗留的 ``_gate_escalation_reason`` /
  ``_rolling_escalation_reason`` 两个字符串属性在 pipeline 侧以 **property
  别名** 形式接到本类上（读 = 查对应 kind 的 reason，写 = register），
  既有读写点零改动即被统一收纳。
- 行为保持：收敛优先级与旧 if-elif 链严格一致
  （BUDGET_TRIP > ROLLING_EVAL > GATE_ESCALATION），
  由 ``test_batch_outcome`` 行为级钉住。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class StopKind(str, Enum):
    """批末停批来源。"""

    NONE = "none"
    BUDGET_TRIP = "budget_trip"            # token/墙钟预算熔断
    ROLLING_EVAL = "eval"                  # 滚动体检不达标（事件 step 名历史用 eval）
    GATE_ESCALATION = "gate_escalation"    # 门禁失明连击/连续告警留章

    @property
    def event_step(self) -> str:
        """收敛时 failure 事件的 step 名（与既有事件流命名保持一致）。"""
        return self.value


#: 停批优先级（数值越大越优先收敛为该类）。**唯一收敛规则**——
#: 与旧 if-elif 链语义严格一致：预算熔断 > 滚动体检 > 门禁升级。
#: 新增 StopKind 成员必须在此登记优先级（缺成员即 KeyError，构造期暴露）。
STOP_PRECEDENCE: dict["StopKind", int] = {
    StopKind.GATE_ESCALATION: 1,
    StopKind.ROLLING_EVAL: 2,
    StopKind.BUDGET_TRIP: 3,
}


@dataclass
class BatchStop:
    """一次停批置位（来源 + 人读理由）。"""

    kind: StopKind
    reason: str


@dataclass
class BatchOutcome:
    """一批运行的停批裁决收集器（零副作用：只存不抛）。"""

    stops: list[BatchStop] = field(default_factory=list)

    def register(self, kind: StopKind, reason: str) -> None:
        """登记一次停批置位。空 reason = 清除该类既有置位（供复位点复用）。"""
        if not reason:
            self.stops = [s for s in self.stops if s.kind is not kind]
            return
        # 同类重复置位：保留首个（首个即触发时刻，语义最准）
        if any(s.kind is kind for s in self.stops):
            return
        self.stops.append(BatchStop(kind=kind, reason=reason))

    def reason_of(self, kind: StopKind) -> str:
        """取指定来源的 reason（未置位返回空串——兼容旧字符串属性读语义）。"""
        for s in self.stops:
            if s.kind is kind:
                return s.reason
        return ""

    def primary(self) -> tuple[StopKind, str]:
        """收敛：返回 (最终停批类别, 理由)。多源并发取优先级最高者，同级先登记者胜。"""
        best: Optional[BatchStop] = None
        for s in self.stops:
            if best is None or STOP_PRECEDENCE[s.kind] > STOP_PRECEDENCE[best.kind]:
                best = s
        return (best.kind, best.reason) if best else (StopKind.NONE, "")

    @property
    def stopped(self) -> bool:
        return bool(self.stops)


class BatchOutcomeMixin:
    """pipeline 侧遗留两个 reason 字符串属性的**兼容别名**。

    ``_gate_escalation_reason`` / ``_rolling_escalation_reason`` 原是实例属性，
    现存储统一迁入 :class:`BatchOutcome`（本类持有 ``_outcome``）。
    属性读 = 查对应 kind 的 reason；写 = register（空串 = 清除该类置位，
    与旧「覆盖赋值」语义一致）。既有读写点零改动即被统一收纳；
    新停批源不得再新增此类字符串属性，直接 register StopKind 成员。
    """

    _outcome: BatchOutcome

    @property
    def outcome(self) -> BatchOutcome:
        """惰性自举：测试/子类可能跳过 __init__，缺 outcome 时现场补建。"""
        if getattr(self, "_outcome", None) is None:
            self._outcome = BatchOutcome()
        return self._outcome

    @property
    def _gate_escalation_reason(self) -> str:
        return self.outcome.reason_of(StopKind.GATE_ESCALATION)

    @_gate_escalation_reason.setter
    def _gate_escalation_reason(self, value: str) -> None:
        self.outcome.register(StopKind.GATE_ESCALATION, value)

    @property
    def _rolling_escalation_reason(self) -> str:
        return self._outcome.reason_of(StopKind.ROLLING_EVAL)

    @_rolling_escalation_reason.setter
    def _rolling_escalation_reason(self, value: str) -> None:
        self.outcome.register(StopKind.ROLLING_EVAL, value)
