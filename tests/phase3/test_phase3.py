"""Phase 3 离线测试（无 LLM / 无网络）

覆盖：TraceStore（追踪+成本）、CostModel（基线+告警）、PromptRegistry（版本+漂移）、
EvalHarness（回归）、TracedLLMClient（span 记录）、AgentService（离线体检+看板）。
"""

from __future__ import annotations

from pathlib import Path

from agent.core.llmops import (
    CostModel,
    EvalHarness,
    PromptRegistry,
    TraceSpan,
    TraceStore,
    TracedLLMClient,
    set_tracer,
)
from agent.core.engine.state_machine import State, StateMachine


def _make_project(tmp_path: Path, n_chapters: int = 0, foreshadows: str = "") -> Path:
    (tmp_path / "chapters").mkdir(parents=True, exist_ok=True)
    (tmp_path / "world.md").write_text("# 测试书\n", encoding="utf-8")
    if foreshadows:
        (tmp_path / "foreshadows.md").write_text(foreshadows, encoding="utf-8")
    sm = StateMachine(tmp_path)
    sm.state = State.WRITING
    sm.progress = {"total_written": n_chapters, "current_chapter": n_chapters}
    sm.save()
    for n in range(1, n_chapters + 1):
        (tmp_path / "chapters" / f"ch{n:03d}.md").write_text(
            f"---\nchapter_title: 第{n}章\n---\n内容{n}。\n", encoding="utf-8"
        )
    return tmp_path


# ============================================================
# 1. TraceStore
# ============================================================
def test_trace_store_totals():
    ts = TraceStore(None)
    ts.record(TraceSpan(model="m", use="creative", tokens_in=100, tokens_out=50, cost=0.01))
    ts.record(TraceSpan(model="m", use="utility", tokens_in=200, tokens_out=80, ok=False, error="x"))
    tot = ts.totals()
    assert tot["calls"] == 2
    assert tot["tokens_total"] == 430
    assert tot["failures"] == 1
    assert tot["cost"] == 0.01
    by_use = ts.by_use()
    assert by_use["creative"]["calls"] == 1
    assert by_use["utility"]["calls"] == 1


def test_trace_store_persist(tmp_path):
    ts = TraceStore(tmp_path)
    ts.record(TraceSpan(model="m", use="creative", tokens_in=10, tokens_out=5))
    ts2 = TraceStore(tmp_path)
    assert ts2.totals()["calls"] == 1


# ============================================================
# 2. CostModel
# ============================================================
def test_cost_model_baseline_scaling():
    cm = CostModel()
    low, high = cm.baseline_tokens("balanced", 300)
    assert (low, high) == (10_000_000, 16_000_000)
    # 缩放
    low2, high2 = cm.baseline_tokens("balanced", 600)
    assert abs(low2 - 20_000_000) < 1 and abs(high2 - 32_000_000) < 1


def test_cost_model_alert():
    cm = CostModel()
    # 在基线上限内
    assert cm.alert_if_over(10_000_000, "balanced", 300) is None
    # 超出上限（16M）
    alert = cm.alert_if_over(20_000_000, "balanced", 300)
    assert alert is not None and "成本告警" in alert


def test_cost_model_estimate_chapter():
    cm = CostModel()
    est = cm.estimate_chapter()
    assert est.tokens_low == 25_000 and est.tokens_high == 35_000


# ============================================================
# 3. PromptRegistry
# ============================================================
def test_prompt_registry_version_and_drift(tmp_path):
    reg = PromptRegistry(tmp_path)
    r1 = reg.register("sys_writer", "你是一个写手")
    assert r1["version"] == 1 and r1["drifted"] is False
    # 相同文本：不更新
    r2 = reg.register("sys_writer", "你是一个写手")
    assert r2["version"] == 1 and r2["updated"] is False
    # 文本变化：版本+1，drifted
    r3 = reg.register("sys_writer", "你是一个顶级写手")
    assert r3["version"] == 2 and r3["drifted"] is True
    # 重新加载
    reg2 = PromptRegistry(tmp_path)
    assert reg2.version("sys_writer") == 2


# ============================================================
# 4. EvalHarness
# ============================================================
def test_eval_harness_record_and_regression(tmp_path):
    h = EvalHarness(tmp_path)
    h.record({"overall_pass": True, "score": 90.0,
              "dimensions": [{"name": "coherence", "passed": True}]})
    # 退化：分数跌 + 维度由通过变未通过
    h.record({"overall_pass": False, "score": 70.0,
              "dimensions": [{"name": "coherence", "passed": False}]})
    issues = h.detect_regression(score_drop=10.0)
    kinds = {i.kind for i in issues}
    assert "score_drop" in kinds
    assert "dim_regress" in kinds
    assert len(h.history()) == 2


# ============================================================
# 5. TracedLLMClient
# ============================================================
class _FakeResp:
    def __init__(self, text, usage=None):
        self.text = text
        self.usage = usage or {"prompt_tokens": 12, "completion_tokens": 8}


class _FakeLLM:
    def chat_structured(self, messages, schema=None, **kw):
        return _FakeResp("结果", {"prompt_tokens": 12, "completion_tokens": 8})

    def chat_utility(self, messages, **kw):
        raise RuntimeError("boom")


def test_traced_llm_records_span():
    ts = TraceStore(None)
    client = TracedLLMClient(_FakeLLM(), model="m", tracer=ts)
    resp = client.chat_structured([], None)
    assert resp.text == "结果"
    tot = ts.totals()
    assert tot["calls"] == 1
    assert tot["tokens_in"] == 12 and tot["tokens_out"] == 8
    assert tot["failures"] == 0


def test_traced_llm_records_failure_and_reraises():
    ts = TraceStore(None)
    client = TracedLLMClient(_FakeLLM(), model="m", tracer=ts)
    try:
        client.chat_utility([])
    except RuntimeError:
        pass
    else:
        raise AssertionError("应原样抛出")
    assert ts.totals()["failures"] == 1


# ============================================================
# 6. AgentService（离线体检 + 看板）
# ============================================================
def test_agent_service_evaluate_offline(tmp_path):
    proj = _make_project(
        tmp_path, n_chapters=8,
        foreshadows=(
            "| ID | 内容 | 埋设 | 预期 | 状态 |\n|---|---|---|---|---|\n"
            "| F-01 | a | ch001 | ch010 | 已埋 |\n"
            "| F-02 | b | ch002 | ch020 | 已埋 |\n"
        ),
    )
    from agent.service.agent_service import AgentService

    svc = AgentService(proj, tier="auto")
    out = svc.run_evaluate(no_rollback=False)
    report = out["report"]
    # 伏笔回收率 0 < 0.9 → 不达标 → 自动回溯
    assert report["overall_pass"] is False
    assert report["rolled_back"] is True
    # 评测回归已记录
    assert svc.eval_harness.latest() is not None
    # 看板含追踪汇总
    assert "trace_totals" in out["llmops"]


def test_agent_service_summarize_and_prompt(tmp_path):
    from agent.service.agent_service import AgentService

    svc = AgentService(tmp_path)
    svc.register_prompt("sys_writer", "你是一个写手")
    summary = svc.summarize()
    assert "trace_totals" in summary
    assert summary["prompt_versions"]["sys_writer"]["version"] == 1
