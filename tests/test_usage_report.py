"""G15 P1-1 · Token 用量汇总测试

断言：
1. run × subagent 二维聚合正确；
2. 聚合口径与 ``TraceStore.totals`` 同源一致；
3. 快照落盘 / 读取 / 增量（diff）正确。
"""

from __future__ import annotations

from agent.core.llmops.trace import TraceSpan, TraceStore
from agent.core.llmops.usage_reporter import UsageReporter, _group_spans


def test_by_run_subagent(tmp_path: Path) -> None:
    p = tmp_path / "proj"
    trace = TraceStore(p)
    trace.record(TraceSpan(model="m", use="creative",
                           tokens_in=100, tokens_out=50, meta={"run_id": "r1", "subagent_id": "planner"}))
    trace.record(TraceSpan(model="m", use="utility",
                           tokens_in=200, tokens_out=100, meta={"run_id": "r1", "subagent_id": "writer"}))
    trace.record(TraceSpan(model="m", use="creative",
                           tokens_in=400, tokens_out=200, meta={"run_id": "r2", "subagent_id": "writer"}))

    rep = UsageReporter(p)
    agg = rep.aggregate()
    assert agg["r1"]["planner"]["tokens_total"] == 150
    assert agg["r1"]["writer"]["tokens_total"] == 300
    assert agg["r2"]["writer"]["tokens_total"] == 600

    by_run = rep.by_run()
    assert by_run["r1"]["calls"] == 2
    assert by_run["r1"]["tokens_total"] == 450
    assert by_run["r2"]["tokens_total"] == 600


def test_same_source_as_trace(tmp_path: Path) -> None:
    p = tmp_path / "proj"
    trace = TraceStore(p)
    trace.record(TraceSpan(model="m", use="creative", tokens_in=120, tokens_out=80))
    trace.record(TraceSpan(model="m", use="utility", tokens_in=30, tokens_out=10))

    rep = UsageReporter(p)
    # 口径与 TraceStore 全局总量一致（同源）
    assert rep.totals()["calls"] == trace.totals()["calls"]
    assert rep.totals()["tokens_total"] == trace.totals()["tokens_total"]
    assert rep.totals()["cost"] == trace.totals()["cost"]


def test_snapshot_store_and_diff(tmp_path: Path) -> None:
    p = tmp_path / "proj"
    trace = TraceStore(p)
    # 先落盘再建 reporter —— 与 test_same_source 相同的「同源读取」时序
    trace.record(TraceSpan(model="m", use="creative", tokens_in=100, tokens_out=50,
                           meta={"run_id": "r1", "subagent_id": "writer"}))
    rep = UsageReporter(p)
    base = rep.snapshot()
    rep.store(base)
    # 再补一笔
    trace.record(TraceSpan(model="m", use="utility", tokens_in=200, tokens_out=100,
                           meta={"run_id": "r1", "subagent_id": "writer"}))
    rep2 = UsageReporter(p)
    cur = rep2.snapshot()
    d = rep2.diff(base, cur)
    assert d["totals"]["calls"] == 1
    assert d["totals"]["tokens_total"] == 300

    # 落盘再读回：base 快照含 1 笔
    assert rep.load_store().get("totals", {}).get("calls") == 1

    # 又记一笔 → 相对 base 是 2 笔增量
    trace.record(TraceSpan(model="m", use="utility", tokens_in=200, tokens_out=100,
                           meta={"run_id": "r1", "subagent_id": "writer"}))
    cur = UsageReporter(p).snapshot()
    d = UsageReporter(p).diff(base, cur)
    assert d["totals"]["calls"] == 2
    assert d["totals"]["tokens_total"] == 300 + 300  # 第2/3笔各 300


def test_group_defaults_main_agent() -> None:
    from agent.core.llmops.trace import TraceSpan
    grabs = _group_spans([
        TraceSpan(model="m", use="c", tokens_in=5, tokens_out=5, meta={}),
    ])
    assert grabs["_"]["main-agent"]["tokens_total"] == 10


def test_store_graceful_missing(tmp_path: Path) -> None:
    rep = UsageReporter(tmp_path / "nonexistent" / "proj")
    assert rep.load_store() == {}