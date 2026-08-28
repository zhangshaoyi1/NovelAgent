"""G7 成本透明测试（T4-T6 验收，纯离线）

覆盖（对齐 PRD §8 / 设计 §8 T4-T6）：
- `build_cost_summary`：stub tracer 注入已知 totals → 返回正确 totals / 基线 / 告警。
- `get_tracer()` 为 NullTracer（无 totals）→ fallback `TraceStore(project_dir).totals()`。
- 异常路径 → `tracked=False` + note 占位（不抛异常）。
- 与 G4 熔断同源：build_cost_summary 与 `_check_budget` 读同一 `get_tracer().totals()`。
- `PipelineResult.to_dict()` 增 `cost` 键、既有键零改动。
- `autowrite --json` 信封含 `cost`；`--no-cost` → cost 置 null；`--no-human-summary` 透传。
- evaluate/appeal 接线 `TracedLLMClient` 后调用次数真实增长（修复 R3-3）。

零网络：LLMClient 全部 monkeypatch 为 fake；tracer 全部 stub / 本地 TraceStore。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from rich.console import Console

from agent.client import LLMResponse
from agent.core.llmops.trace import NullTracer, TraceSpan, TraceStore, get_tracer, set_tracer
from agent.workflows.agentic_pipeline import AgenticPipelineWorkflow, PipelineResult
from tests.conftest import make_project


# ============================================================
# 夹具：全局 tracer 恢复（共享知识 #12 / 新增风险缓解）
# ============================================================
@pytest.fixture(autouse=True)
def _reset_tracer():
    yield
    set_tracer(NullTracer())


# ============================================================
# 1. build_cost_summary：stub tracer → 正确 totals / 基线 / 告警
# ============================================================
def test_build_cost_summary_totals(monkeypatch, tmp_path: Path) -> None:
    from agent.core.llmops.cost import build_cost_summary

    totals = {
        "calls": 5, "tokens_in": 1000, "tokens_out": 500, "tokens_total": 1500,
        "failures": 1, "avg_latency_ms": 123.45, "cost": 0.0,
    }
    monkeypatch.setattr(
        "agent.core.llmops.trace.get_tracer",
        lambda: SimpleNamespace(totals=lambda: totals),
    )
    cost = build_cost_summary(str(tmp_path), "balanced", 12)
    assert cost["tracked"] is True
    assert cost["calls"] == 5
    assert cost["tokens_in"] == 1000
    assert cost["tokens_out"] == 500
    assert cost["tokens_total"] == 1500
    assert cost["failures"] == 1
    assert cost["avg_latency_ms"] == 123.45
    assert cost["cost"] == 0.0
    # balanced 12 章基线：10M/300*12=400k low，16M/300*12=640k high
    assert cost["baseline_low"] == pytest.approx(400_000)
    assert cost["baseline_high"] == pytest.approx(640_000)
    assert cost["alert"] is None, "1500 tokens 远低于基线，不应告警"


def test_build_cost_summary_alert(monkeypatch, tmp_path: Path) -> None:
    from agent.core.llmops.cost import build_cost_summary

    totals = {
        "calls": 1, "tokens_in": 0, "tokens_out": 2_000_000, "tokens_total": 2_000_000,
        "failures": 0, "avg_latency_ms": 0.0, "cost": 0.0,
    }
    monkeypatch.setattr(
        "agent.core.llmops.trace.get_tracer",
        lambda: SimpleNamespace(totals=lambda: totals),
    )
    cost = build_cost_summary(str(tmp_path), "balanced", 12)
    assert cost["alert"] is not None, "2M tokens 超过 640k 基线上限应告警"
    assert "成本告警" in cost["alert"]


def test_build_cost_summary_chapters_default_from_files(tmp_path: Path) -> None:
    """chapters=None 时按当前章节文件数；无章节则 300。"""
    from agent.core.llmops.cost import build_cost_summary

    totals = {"calls": 0, "tokens_in": 0, "tokens_out": 0, "tokens_total": 0,
              "failures": 0, "avg_latency_ms": 0.0, "cost": 0.0}
    with patch("agent.core.llmops.trace.get_tracer",
               lambda: SimpleNamespace(totals=lambda: totals)):
        # 无章节 → 300 章基线：low=10M, high=16M
        cost = build_cost_summary(str(tmp_path), "balanced", None)
        assert cost["baseline_low"] == pytest.approx(10_000_000)
        assert cost["baseline_high"] == pytest.approx(16_000_000)

        # 有 3 章 → 3 章基线：low=100k, high=160k
        d = make_project(tmp_path, n_chapters=3)
        cost3 = build_cost_summary(str(d), "balanced", None)
        assert cost3["baseline_low"] == pytest.approx(100_000)
        assert cost3["baseline_high"] == pytest.approx(160_000)


# ============================================================
# 2. NullTracer fallback → TraceStore(project_dir).totals()
# ============================================================
def test_build_cost_summary_fallback_to_trace_store(tmp_path: Path) -> None:
    from agent.core.llmops.cost import build_cost_summary

    store = TraceStore(tmp_path)
    store.record(TraceSpan(model="fake", use="utility", tokens_in=200, tokens_out=100))
    # get_tracer() 返回 NullTracer（无 totals 方法）→ fallback 文件存储
    with patch("agent.core.llmops.trace.get_tracer", lambda: NullTracer()):
        cost = build_cost_summary(str(tmp_path), "balanced", 12)
    assert cost["tracked"] is True
    assert cost["calls"] == 1
    assert cost["tokens_in"] == 200
    assert cost["tokens_out"] == 100
    assert cost["tokens_total"] == 300


# ============================================================
# 3. 异常路径 → tracked=False + note（不抛异常）
# ============================================================
def test_build_cost_summary_exception_degraded(tmp_path: Path) -> None:
    from agent.core.llmops.cost import build_cost_summary

    def _boom():
        raise RuntimeError("trace broken")

    with patch("agent.core.llmops.trace.get_tracer", _boom):
        cost = build_cost_summary(str(tmp_path), "balanced", 12)  # 不应抛异常
    assert cost["tracked"] is False
    assert cost["note"] == "本次调用未追踪（仅统计已有记录）"
    assert cost["calls"] == 0


# ============================================================
# 4. 与 G4 熔断同源（共享知识 #5）
# ============================================================
def test_build_cost_summary_same_source_as_g4_breaker(tmp_path: Path, monkeypatch) -> None:
    from agent.core.llmops.cost import build_cost_summary

    totals = {"calls": 3, "tokens_in": 700_000, "tokens_out": 0, "tokens_total": 700_000,
              "failures": 0, "avg_latency_ms": 1.0, "cost": 0.0}
    monkeypatch.setattr(
        "agent.core.llmops.trace.get_tracer",
        lambda: SimpleNamespace(totals=lambda: totals),
    )
    cost = build_cost_summary(str(tmp_path), "balanced", 12)
    assert cost["tokens_total"] == 700_000
    assert cost["alert"] is not None, "700k > 640k 基线上限应告警"

    p = AgenticPipelineWorkflow(
        project_dir=tmp_path, cost_tier="balanced", target_chapters=12,
        console=Console(quiet=True),
    )
    assert p._check_budget("test") is True, "G4 熔断用同源数据应同样判定超限"


# ============================================================
# 5. PipelineResult.to_dict 增 cost（只增不删）
# ============================================================
def test_pipeline_result_to_dict_cost() -> None:
    r = PipelineResult(planned=True, cost={"tracked": True, "calls": 3})
    d = r.to_dict()
    assert d["cost"] == {"tracked": True, "calls": 3}
    for key in ("planned", "chapters_written", "final_chapter", "health_report",
                "escalated", "escalated_reason", "blocked", "block_reason", "engine",
                "tripped", "schema_degraded", "guardrails"):
        assert key in d, f"既有键 {key} 必须保留（只增不删）"
    # G8（拍板 6）：PipelineResult.to_dict 只增 mainline/ending 两键（设计 §5.2）
    assert "mainline" in d and "ending" in d, "G8 只增 mainline/ending（只增不删）"
    # G9（拍板 6）：PipelineResult.to_dict 再增 progress_file/failures/stream/summary 四键
    #（设计 §7.2：to_dict 只增 4 键；既有 12 键 + cost + mainline + ending + G9×4 = 19）
    assert "progress_file" in d and "failures" in d and "stream" in d and "summary" in d, (
        "G9 只增 progress_file/failures/stream/summary（只增不删）"
    )
    assert len(d) == 19, "既有 12 键 + cost + mainline + ending + G9×4 = 19"


# ============================================================
# 6. autowrite --json 信封含 cost / --no-cost 置 null / human_summary 透传
# ============================================================
def _capture_pipeline_run(monkeypatch, captured_kwargs: dict, result: PipelineResult) -> None:
    class _CapturingPipeline(AgenticPipelineWorkflow):
        def __init__(self, **kwargs):
            captured_kwargs.update(kwargs)
            super().__init__(**kwargs)

        def run(self):
            return result

    monkeypatch.setattr(
        "agent.workflows.agentic_pipeline.AgenticPipelineWorkflow",
        _CapturingPipeline,
    )


def _call_autowrite(**overrides) -> None:
    from agent.cli.commands.autowrite import autowrite

    kwargs = {
        "project_dir": str(Path("tmp")),
        "json_output": True,
        "env_file": None,
        "brief": "测试",
        "chapters": 0,
        "mode": "auto",
        "no_eval": True,
        "rollback_window": 5,
        "max_rollback": 3,
        "max_time": None,
        "cost_tier": "balanced",
        "budget_margin": 1.0,
        "llm_timeout": None,
        "appeal_gate": True,
        "no_appeal_gate": False,
        "appeal_threshold": 60,
        "appeal_window": 1,
        "no_human_summary": False,
        "no_cost": False,
    }
    kwargs.update(overrides)
    autowrite(**kwargs)


def test_autowrite_json_envelope_has_cost(tmp_path: Path, monkeypatch, capsys) -> None:
    captured: dict = {}
    result = PipelineResult(planned=True, cost={"tracked": True, "calls": 7})
    _capture_pipeline_run(monkeypatch, captured, result)
    _call_autowrite(project_dir=str(tmp_path), json_output=True)
    lines = [l for l in capsys.readouterr().out.strip().split("\n") if l.strip()]
    envelope = json.loads(lines[-1])
    assert envelope["success"] is True
    assert envelope["cost"] == {"tracked": True, "calls": 7}
    # human_summary 默认开 → 透传 True
    assert captured["human_summary"] is True


def test_autowrite_no_cost_json_null(tmp_path: Path, monkeypatch, capsys) -> None:
    captured: dict = {}
    result = PipelineResult(planned=True, cost={"tracked": True, "calls": 7})
    _capture_pipeline_run(monkeypatch, captured, result)
    _call_autowrite(project_dir=str(tmp_path), json_output=True, no_cost=True)
    lines = [l for l in capsys.readouterr().out.strip().split("\n") if l.strip()]
    envelope = json.loads(lines[-1])
    assert envelope["cost"] is None, "--no-cost 后 cost 应置 null"


def test_autowrite_no_human_summary_passthrough(tmp_path: Path, monkeypatch, capsys) -> None:
    captured: dict = {}
    result = PipelineResult(planned=True, cost=None)
    _capture_pipeline_run(monkeypatch, captured, result)
    _call_autowrite(project_dir=str(tmp_path), json_output=True, no_human_summary=True)
    assert captured["human_summary"] is False, "--no-human-summary 应透传 human_summary=False"


# ============================================================
# 7. evaluate/appeal 接 tracer：本次调用真实计入（修复 R3-3）
# ============================================================
class _FakeEvalLLM:
    """evaluate 用 fake LLM：返回可解析的维度 JSON，统计调用次数。"""

    def __init__(self) -> None:
        self.calls = 0

    def chat_utility(self, messages, **kwargs) -> LLMResponse:
        self.calls += 1
        return LLMResponse(
            text='{"value": 0, "rationale": "ok", "issues": []}',
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )


class _FakeAppealLLM:
    """appeal 用 fake LLM：返回可解析的迷爱看 JSON，统计调用次数。"""

    def __init__(self) -> None:
        self.calls = 0

    def chat_utility(self, messages, **kwargs) -> LLMResponse:
        self.calls += 1
        return LLMResponse(
            text=json.dumps({
                "dimensions": {"hook_strength": 80, "payoff_density": 80, "immersion": 80,
                               "character_arc": 80, "world_novelty": 80, "emotion_curve": 80},
                "one_liner": "很精彩",
                "suggestions": ["加强悬念"],
            }, ensure_ascii=False),
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )


def test_evaluate_wiring_tracer_calls_grow(tmp_path: Path, monkeypatch, capsys) -> None:
    from agent.cli.commands.evaluate import evaluate
    from agent.core.engine.state_machine import State

    d = make_project(tmp_path, n_chapters=3, state=State.WRITING)
    fake = _FakeEvalLLM()
    monkeypatch.setattr("agent.client.LLMClient", lambda *a, **kw: fake)
    set_tracer(TraceStore(d))
    before = get_tracer().totals()["calls"]

    evaluate(project_dir=str(d), json_output=True, env_file=None, no_rollback=True,
             auto_repair=False, rollback_window=5, max_rollback=3, real_score=True,
             no_human_summary=False, no_cost=False)

    after = get_tracer().totals()["calls"]
    assert after > before, "evaluate 接 TracedLLMClient 后本次调用应真实计入 trace（修复 R3-3）"
    assert after - before == fake.calls == 5, "五维真 LLM 评分各记录一次 span"

    lines = [l for l in capsys.readouterr().out.strip().split("\n") if l.strip()]
    envelope = json.loads(lines[-1])
    assert "cost" in envelope, "--json 信封应含 cost"
    assert envelope["cost"]["tracked"] is True
    assert envelope["report"]["summary"] is not None, "默认 human_summary 开 → summary 已填充"


def test_appeal_wiring_tracer_calls_grow(tmp_path: Path, monkeypatch, capsys) -> None:
    from agent.cli.commands.appeal import appeal
    from agent.core.engine.state_machine import State

    d = make_project(tmp_path, n_chapters=3, state=State.WRITING)
    fake = _FakeAppealLLM()
    monkeypatch.setattr("agent.client.LLMClient", lambda *a, **kw: fake)
    set_tracer(TraceStore(d))
    before = get_tracer().totals()["calls"]

    appeal(project_dir=str(d), chapter=1, file="", json_output=True, env_file=None,
           no_human_summary=False, no_cost=False)

    after = get_tracer().totals()["calls"]
    assert after > before, "appeal 接 TracedLLMClient 后本次调用应真实计入 trace（修复 R3-3）"
    assert after - before == fake.calls == 1, "一次 score_chapter 记录一次 span"

    lines = [l for l in capsys.readouterr().out.strip().split("\n") if l.strip()]
    envelope = json.loads(lines[-1])
    assert "cost" in envelope, "--json 信封应含 cost"
    assert envelope["cost"]["tracked"] is True
    assert envelope["report"]["summary_lines"], "默认 human_summary 开 → summary_lines 已填充"


def test_evaluate_no_cost_json_null(tmp_path: Path, monkeypatch, capsys) -> None:
    from agent.cli.commands.evaluate import evaluate
    from agent.core.engine.state_machine import State

    d = make_project(tmp_path, n_chapters=1, state=State.WRITING)
    monkeypatch.setattr("agent.client.LLMClient", lambda *a, **kw: _FakeEvalLLM())
    evaluate(project_dir=str(d), json_output=True, env_file=None, no_rollback=True,
             auto_repair=False, rollback_window=5, max_rollback=3, real_score=True,
             no_human_summary=False, no_cost=True)
    lines = [l for l in capsys.readouterr().out.strip().split("\n") if l.strip()]
    envelope = json.loads(lines[-1])
    assert "cost" in envelope and envelope["cost"] is None, "--no-cost 后 cost 应置 null"
