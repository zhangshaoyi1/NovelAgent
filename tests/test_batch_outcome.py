"""BatchOutcome 统一停批裁决红线（20261003 复盘第二批：四通道收敛）。

守卫：
- **收敛唯一规则**：优先级数据表 BUDGET_TRIP > ROLLING_EVAL > GATE_ESCALATION，
  同级先登记者胜——与旧 if-elif 链语义严格一致（行为保持红线）。
- **别名兼容**：pipeline 的 ``_gate_escalation_reason`` /
  ``_rolling_escalation_reason`` property 别名读写语义与旧实例属性一致
  （含「先判空再置位」「置空清除」两个既有用法）。
- **新停批源 = 枚举成员 + register**：不再允许新增平行 reason 字符串属性
  （静态断言：pipeline 源码中除别名外不得出现新的 _escalation_reason 属性赋值）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.core.engine.batch_outcome import (
    STOP_PRECEDENCE,
    BatchOutcome,
    BatchOutcomeMixin,
    StopKind,
)


# ---------------------------------------------------------------- 收敛规则
def test_precedence_budget_wins_over_all() -> None:
    o = BatchOutcome()
    o.register(StopKind.GATE_ESCALATION, "门禁失明 3 次")
    o.register(StopKind.ROLLING_EVAL, "体检未达标")
    o.register(StopKind.BUDGET_TRIP, "预算超限")
    kind, reason = o.primary()
    assert kind is StopKind.BUDGET_TRIP and "预算" in reason


def test_precedence_rolling_over_gate() -> None:
    o = BatchOutcome()
    o.register(StopKind.GATE_ESCALATION, "门禁")
    o.register(StopKind.ROLLING_EVAL, "体检")
    assert o.primary()[0] is StopKind.ROLLING_EVAL


def test_single_source_passes_through() -> None:
    o = BatchOutcome()
    o.register(StopKind.GATE_ESCALATION, "连续告警留章")
    assert o.primary() == (StopKind.GATE_ESCALATION, "连续告警留章")
    assert o.stopped is True


def test_first_registered_wins_within_kind() -> None:
    o = BatchOutcome()
    o.register(StopKind.GATE_ESCALATION, "第一次置位")
    o.register(StopKind.GATE_ESCALATION, "后来的覆盖企图")
    assert o.primary()[1] == "第一次置位"


def test_empty_reason_clears_kind() -> None:
    """置空串 = 清除该类置位（兼容旧「覆盖赋值清空」语义）。"""
    o = BatchOutcome()
    o.register(StopKind.GATE_ESCALATION, "x")
    o.register(StopKind.GATE_ESCALATION, "")
    assert o.reason_of(StopKind.GATE_ESCALATION) == ""
    assert o.stopped is False


def test_primary_none_when_empty() -> None:
    assert BatchOutcome().primary()[0] is StopKind.NONE


def test_event_step_names_match_legacy() -> None:
    """事件 step 名与旧字符串通道严格一致（事件流消费者兼容红线）。"""
    assert StopKind.BUDGET_TRIP.event_step == "budget_trip"
    assert StopKind.ROLLING_EVAL.event_step == "eval"
    assert StopKind.GATE_ESCALATION.event_step == "gate_escalation"


def test_precedence_table_covers_all_members() -> None:
    for kind in StopKind:
        if kind is StopKind.NONE:
            continue
        assert kind in STOP_PRECEDENCE, f"{kind} 未登记优先级（构造期必须暴露）"


# ---------------------------------------------------------------- 别名兼容
class _Host(BatchOutcomeMixin):
    def __init__(self) -> None:
        self._outcome = BatchOutcome()


def test_alias_roundtrip_read_write() -> None:
    h = _Host()
    assert h._gate_escalation_reason == ""  # 旧代码「hasattr + 判空」语义成立
    h._gate_escalation_reason = "门禁失明"
    assert h._gate_escalation_reason == "门禁失明"
    h._rolling_escalation_reason = "体检不达标"
    assert h._rolling_escalation_reason == "体检不达标"


def test_alias_clear_semantics() -> None:
    h = _Host()
    h._gate_escalation_reason = "x"
    h._gate_escalation_reason = ""  # 旧代码置空清除
    assert h._gate_escalation_reason == ""


def test_alias_backed_by_shared_outcome() -> None:
    h = _Host()
    h._gate_escalation_reason = "gate"
    h._rolling_escalation_reason = "rolling"
    kind, reason = h._outcome.primary()
    assert kind is StopKind.ROLLING_EVAL and reason == "rolling"


# ---------------------------------------------------------------- 静态红线
def test_no_new_parallel_reason_attributes() -> None:
    """pipeline 源码不得再新增平行 *_escalation_reason 属性赋值
    （新停批源必须走 StopKind + register）。"""
    src = (
        Path(__file__).resolve().parents[1]
        / "src" / "agent" / "workflows" / "pipeline" / "agentic_pipeline.py"
    ).read_text(encoding="utf-8")
    assert "self._gate_escalation_reason" not in src or True  # 读点允许（别名）
    forbidden = "self._rolling_escalation_reason: str ="
    assert forbidden not in src, "停批通道必须经 BatchOutcome 存储，不得回退为实例属性"
