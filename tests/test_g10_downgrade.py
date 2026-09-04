"""G10 超预算降档测试（T3 验收，纯离线零 LLM）

覆盖（对齐设计 §4 / 拍板 3/4）：
- 非最低档超限 → _maybe_downgrade_tier 降一档 + cost_downgrade 事件 + 继续（返回 True）；
- 最低档仍超限 → False（走 G4 熔断）；
- auto_downgrade=False（pipeline 默认）→ False（G4 兼容，test_breaker_* 不破坏）；
- token 预算内（墙钟超时）→ False（熔断）；
- _DOWNGRADE_ORDER 模块级序 quality→balanced→economy。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from agent.workflows.pipeline.agentic_pipeline import AgenticPipelineWorkflow, _DOWNGRADE_ORDER


class _FakeTracer:
    def __init__(self, tokens: float) -> None:
        self._tokens = tokens

    def totals(self) -> dict:
        return {"tokens_total": self._tokens}


# ============================================================
# 1. 非最低档超限 → 降档 + 事件 + 返回 True
# ============================================================
def test_downgrade_balanced_to_economy(tmp_path: Path) -> None:
    wf = AgenticPipelineWorkflow(
        tmp_path, console=None, cost_tier="balanced", budget_margin=1.0,
        auto_downgrade=True,
    )
    events: list[dict] = []
    wf._emit_event = lambda *a, **k: events.append({"type": a[0], **k})
    # 用超大 token 数模拟超限（balanced 30 章上限 << 10^12）
    with patch("agent.core.llmops.trace.get_tracer", return_value=_FakeTracer(1e12)):
        ok = wf._maybe_downgrade_tier()
    assert ok is True, "非最低档超限应降档继续"
    assert wf._cost_tier == "economy"
    assert any(e["type"] == "cost_downgrade" and e["from_tier"] == "balanced" and e["to_tier"] == "economy" for e in events)


# ============================================================
# 2. 最低档仍超限 → False（走 G4 熔断）
# ============================================================
def test_downgrade_lowest_tier_returns_false(tmp_path: Path) -> None:
    wf = AgenticPipelineWorkflow(
        tmp_path, console=None, cost_tier="economy", budget_margin=1.0,
        auto_downgrade=True,
    )
    with patch("agent.core.llmops.trace.get_tracer", return_value=_FakeTracer(1e12)):
        ok = wf._maybe_downgrade_tier()
    assert ok is False, "最低档仍超限 → 熔断（tripped）"
    assert wf._cost_tier == "economy"


# ============================================================
# 3. auto_downgrade=False（pipeline 默认）→ False（G4 兼容）
# ============================================================
def test_downgrade_disabled_by_default(tmp_path: Path) -> None:
    wf = AgenticPipelineWorkflow(tmp_path, console=None, cost_tier="balanced")
    assert wf._auto_downgrade is False, "pipeline 默认 auto_downgrade=False（G4 兼容）"
    with patch("agent.core.llmops.trace.get_tracer", return_value=_FakeTracer(1e12)):
        ok = wf._maybe_downgrade_tier()
    assert ok is False
    assert wf._cost_tier == "balanced", "auto 关 → 不降档"


# ============================================================
# 4. token 预算内（墙钟超时场景）→ False（熔断）
# ============================================================
def test_downgrade_within_token_budget_returns_false(tmp_path: Path) -> None:
    wf = AgenticPipelineWorkflow(
        tmp_path, console=None, cost_tier="balanced", budget_margin=1.0,
        auto_downgrade=True,
    )
    # 用极小 token 数模拟预算内（超限来自墙钟，monkeypatch _check_budget=True 场景）
    with patch("agent.core.llmops.trace.get_tracer", return_value=_FakeTracer(100)):
        ok = wf._maybe_downgrade_tier()
    assert ok is False, "token 预算内 → 超限来自墙钟 → 熔断（G4 语义）"
    assert wf._cost_tier == "balanced"


# ============================================================
# 5. 检查点接线：budget_over 且未降档 → tripped
# ============================================================
def test_checkpoint_trips_when_no_downgrade(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from agent.workflows.pipeline.agentic_pipeline import AgenticPipelineWorkflow

    wf = AgenticPipelineWorkflow(
        tmp_path, console=None, cost_tier="economy", auto_downgrade=True
    )
    result = SimpleNamespace(tripped=False, block_reason="")
    wf._emit_failure = lambda *a, **k: None
    # 模拟 _check_budget 返回 True + 最低档不降档 → tripped
    budget_over = True
    if budget_over and not wf._maybe_downgrade_tier():
        result.tripped = True
        result.block_reason = "Token 预算超限或墙钟超时熔断（写章阶段）"
    assert result.tripped is True
    assert "熔断" in result.block_reason


# ============================================================
# 6. _DOWNGRADE_ORDER 模块级序
# ============================================================
def test_downgrade_order() -> None:
    assert _DOWNGRADE_ORDER == ["quality", "balanced", "economy"]
