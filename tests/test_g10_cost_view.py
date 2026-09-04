"""G10 写中成本视图测试（T2 验收，纯离线零 LLM）

覆盖（对齐设计 §3 / 拍板 2）：
- ProgressEventBus cost_provider 接线：事件附加 tokens_used/budget/remaining；
- 未接线（cost_provider=None）→ G9 行为逐字节一致（无成本字段）；
- provider 异常 → 事件不丢（降级不阻断）；
- pipeline._current_cost_fields 与 _check_budget 同源（CostModel.baseline_tokens）。
"""

from __future__ import annotations

from pathlib import Path

from agent.core.engine.events import ProgressEventBus


class _FakeTracer:
    def totals(self) -> dict:
        return {"tokens_total": 3_200_000}


# ============================================================
# 1. cost_provider 接线：事件附加三成本字段
# ============================================================
def test_bus_attaches_cost_fields() -> None:
    captured: list[dict] = []

    def on_event(ev: dict) -> None:
        captured.append(ev)

    bus = ProgressEventBus(
        on_event=on_event,
        progress_file=None,
        cost_provider=lambda: {
            "tokens_used": 3_200_000.0,
            "tokens_budget": 10_000_000.0,
            "tokens_remaining": 6_800_000.0,
        },
    )
    bus.emit("chapter_start", chapter=1, total=30)
    ev = captured[0]
    assert ev["tokens_used"] == 3_200_000.0
    assert ev["tokens_budget"] == 10_000_000.0
    assert ev["tokens_remaining"] == 6_800_000.0
    assert ev["seq"] == 1 and ev["type"] == "chapter_start"


# ============================================================
# 2. 未接线（cost_provider=None）→ 无成本字段（G9 行为一致）
# ============================================================
def test_bus_no_provider_no_cost_fields() -> None:
    captured: list[dict] = []

    bus = ProgressEventBus(on_event=captured.append, progress_file=None)
    bus.emit("chapter_done", chapter=1, words=2000)
    ev = captured[0]
    assert "tokens_used" not in ev
    assert "tokens_budget" not in ev
    assert "tokens_remaining" not in ev


# ============================================================
# 3. provider 异常 → 事件不丢（降级不阻断）
# ============================================================
def test_bus_provider_exception_keeps_event() -> None:
    captured: list[dict] = []

    def boom() -> dict:
        raise RuntimeError("provider 故障")

    bus = ProgressEventBus(on_event=captured.append, progress_file=None, cost_provider=boom)
    bus.emit("chapter_done", chapter=1, words=2000)
    assert len(captured) == 1
    assert captured[0]["type"] == "chapter_done"
    assert "tokens_used" not in captured[0]


# ============================================================
# 4. pipeline._current_cost_fields 与 _check_budget 同源
# ============================================================
def test_pipeline_cost_fields_same_source(tmp_path: Path) -> None:
    from unittest.mock import patch

    from agent.workflows.pipeline.agentic_pipeline import AgenticPipelineWorkflow

    wf = AgenticPipelineWorkflow(
        tmp_path, console=None, cost_tier="balanced", budget_margin=1.0
    )
    with patch("agent.core.llmops.trace.get_tracer", return_value=_FakeTracer()):
        fields = wf._current_cost_fields()
    assert fields["tokens_used"] == 3_200_000.0
    # budget 与 _check_budget 同源：baseline_tokens("balanced", target)[1] * margin
    from agent.core.llmops.cost import CostModel

    _, limit = CostModel().baseline_tokens("balanced", wf._resolve_target())
    assert fields["tokens_budget"] == limit * 1.0
    assert fields["tokens_remaining"] == fields["tokens_budget"] - 3_200_000.0
