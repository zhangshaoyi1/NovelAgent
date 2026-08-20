"""G4 熔断测试（P0-1 验收）：验证 _check_budget 与熔断检查点行为。

纯离线：用 monkeypatch 隔离真实 LLM 调用，直接控制 tracer totals 与墙钟。
覆盖 PRD §7 验收①：超时熔断不卡死、预算超限主动中止、已写产物保留。

设计要点：
- 生产主路径 ``run()`` 会调用真实 M1~M4（``_autoplan_full_book``），离线环境无 LLM，
  故 run() 集成测试统一 ``skip_planning=True`` 将 ``_autoplan_full_book`` 置为 no-op，
  仅验证写章/评测阶段的预算熔断与产物保留语义（规划阶段熔断已由单元层 ``_check_budget`` 覆盖）。
- ``_resolve_target()`` 在离线 stub 下回退为 100 章，baseline 过大；测试统一传
  ``target_chapters=12`` 使 balanced 档 baseline 高限 = 640k，token 阈值可预期。
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from rich.console import Console

from agent.core.llmops.trace import TraceStore, get_tracer, set_tracer
from agent.workflows.agentic_pipeline import AgenticPipelineWorkflow, PipelineResult
from tests._g3_fakes import _StubEditor, _StubMemory, _StubPlanner, _StubWriter, _make_plan


# ============================================================
# 辅助：构造带 fake LLM / stub 的 pipeline
# ============================================================
def _make_pipeline(
    tmp_path: Path,
    *,
    max_time: int | None = None,
    cost_tier: str = "balanced",
    budget_margin: float = 1.0,
    eval_enabled: bool = False,
    target_chapters: int = 12,
    skip_planning: bool = False,
    **kw,
) -> AgenticPipelineWorkflow:
    """构造最小可用 pipeline（planner/writer/editor/memory 全 stub）。

    skip_planning=True 时把 ``_autoplan_full_book`` 置为 no-op，避免触发真实 M1~M4
    （离线无 LLM），使 run() 集成测试聚焦写章/评测阶段的熔断语义。
    """
    p = AgenticPipelineWorkflow(
        project_dir=tmp_path,
        llm_client=None,  # 不使用真实 LLM
        brief="测试用创作思路",
        planner=_StubPlanner(_make_plan()),
        writer_workflow=_StubWriter(),
        editor=_StubEditor(),
        memory=_StubMemory(),
        eval_enabled=eval_enabled,
        target_chapters=target_chapters,
        max_time=max_time,
        cost_tier=cost_tier,
        budget_margin=budget_margin,
        console=Console(quiet=True),
        **kw,
    )
    if skip_planning:
        p._autoplan_full_book = lambda: None  # type: ignore[method-assign]
    return p


def _set_tracer_totals(tokens_total: int) -> None:
    """注入指定 token 总数到全局 tracer（绕过真实 trace）。"""
    store = TraceStore(Path("/tmp/g4_breaker_trace"))
    from agent.core.llmops.trace import TraceSpan

    span = TraceSpan(model="fake", use="creative", tokens_in=tokens_total, tokens_out=0)
    store.record(span)
    set_tracer(store)


# ============================================================
# 1. 预算内 → _check_budget() 返回 False
# ============================================================
def test_check_budget_within_limit(tmp_path: Path, monkeypatch) -> None:
    """预算内：已用 token 远低于基线，_check_budget 返回 False。"""
    p = _make_pipeline(tmp_path)
    p._start_time = time.monotonic()
    monkeypatch.setattr(
        "agent.core.llmops.trace.get_tracer",
        lambda: SimpleNamespace(totals=lambda: {"tokens_total": 100}),
    )
    assert p._check_budget("test") is False, "预算内不应触发熔断"


# ============================================================
# 2. Token 超限 → _check_budget() 返回 True
# ============================================================
def test_check_budget_exceeds_token_limit(tmp_path: Path, monkeypatch) -> None:
    """Token 超限：已用 token > 基线 × margin → _check_budget 返回 True。"""
    p = _make_pipeline(tmp_path)
    p._start_time = time.monotonic()
    # balanced 12 章基线 high = 640k；注入 2M 远超上限
    monkeypatch.setattr(
        "agent.core.llmops.trace.get_tracer",
        lambda: SimpleNamespace(totals=lambda: {"tokens_total": 2_000_000}),
    )
    assert p._check_budget("test_token") is True, "Token 超限应触发熔断（返回 True）"


def test_breaker_tripped_flag_set_on_token_exceed(tmp_path: Path, monkeypatch) -> None:
    """Token 超限熔断：run() 写章前触发后 result.tripped==True（设计：trip 仅置 tripped）。"""
    p = _make_pipeline(tmp_path, skip_planning=True)
    p._start_time = time.monotonic()
    monkeypatch.setattr(
        "agent.core.llmops.trace.get_tracer",
        lambda: SimpleNamespace(totals=lambda: {"tokens_total": 2_000_000}),
    )
    result = p.run()
    assert result.tripped is True, "Token 超限后 result.tripped 应为 True"
    assert "熔断" in result.block_reason, (
        f"block_reason 应含熔断信息，实际：{result.block_reason}"
    )


# ============================================================
# 3. 墙钟超时 → _check_budget() 返回 True
# ============================================================
def test_check_budget_exceeds_wall_clock(tmp_path: Path, monkeypatch) -> None:
    """墙钟超时：--max-time 已过 → _check_budget 返回 True。"""
    p = _make_pipeline(tmp_path, max_time=1)
    p._start_time = time.monotonic() - 2  # 已超过 1 秒上限
    monkeypatch.setattr(
        "agent.core.llmops.trace.get_tracer",
        lambda: SimpleNamespace(totals=lambda: {"tokens_total": 100}),
    )
    assert p._check_budget("test_wall") is True, "墙钟超时应触发熔断（返回 True）"


def test_breaker_wall_clock_reason_contains_chaoShi(tmp_path: Path, monkeypatch) -> None:
    """熔断（墙钟/Token 任一）后 run() 设置 tripped 且 block_reason 含"墙钟超时"。

    说明：run() 内部在开头重置 _start_time，故无法从外部注入"已流逝墙钟"触发；
    这里直接令 _check_budget 返回 True 模拟任一类熔断，验证 run() 的熔断收尾语义。
    """
    p = _make_pipeline(tmp_path, skip_planning=True)
    p._start_time = time.monotonic()
    monkeypatch.setattr(p, "_check_budget", lambda step: True)
    result = p.run()
    assert result.tripped is True, "熔断后应置 result.tripped=True"
    assert "墙钟超时" in result.block_reason, (
        f"block_reason 应含'墙钟超时'，实际：{result.block_reason}"
    )


# ============================================================
# 4. 熔断后已写章节保留，不删除
# ============================================================
def test_breaker_preserves_written_chapters(tmp_path: Path, monkeypatch) -> None:
    """熔断后已写章节保留：已落盘的章节文件不被删除。"""
    chapters_dir = tmp_path / "chapters"
    chapters_dir.mkdir(parents=True, exist_ok=True)
    (chapters_dir / "ch_001.md").write_text("# 第一章\n已写内容", encoding="utf-8")

    from agent.core.state_machine import StateMachine, State

    sm = StateMachine(tmp_path)
    sm.state = State.WRITING
    sm.progress = {"total_written": 1}
    sm.save()

    p = _make_pipeline(tmp_path, skip_planning=True)
    p._start_time = time.monotonic()
    monkeypatch.setattr(
        "agent.core.llmops.trace.get_tracer",
        lambda: SimpleNamespace(totals=lambda: {"tokens_total": 2_000_000}),
    )
    result = p.run()
    assert result.tripped is True
    assert (chapters_dir / "ch_001.md").exists(), "熔断后已写章节应保留"
    assert (chapters_dir / "ch_001.md").read_text(encoding="utf-8") == "# 第一章\n已写内容"


# ============================================================
# 5. result.tripped=True → 评测被跳过
# ============================================================
def test_breaker_skips_eval_after_tripped(tmp_path: Path, monkeypatch) -> None:
    """熔断后评测跳过：tripped=True 时 run() 不调用 evaluator。"""
    eval_called = {"count": 0}

    class _StubEvaluator:
        def evaluate_with_repair(self, rewriter):
            eval_called["count"] += 1
            return None

    p = _make_pipeline(
        tmp_path, skip_planning=True, eval_enabled=True, evaluator=_StubEvaluator()
    )
    p._start_time = time.monotonic()
    monkeypatch.setattr(
        "agent.core.llmops.trace.get_tracer",
        lambda: SimpleNamespace(totals=lambda: {"tokens_total": 2_000_000}),
    )
    result = p.run()
    assert result.tripped is True
    assert eval_called["count"] == 0, "熔断后不应调用 evaluator.evaluate_with_repair"


# ============================================================
# 6. 预算内正常跑完，不触发熔断
# ============================================================
def test_breaker_no_trip_within_budget(tmp_path: Path, monkeypatch) -> None:
    """预算内：正常跑完不触发熔断，tripped=False。"""
    p = _make_pipeline(tmp_path, skip_planning=True)
    p._start_time = time.monotonic()
    monkeypatch.setattr(
        "agent.core.llmops.trace.get_tracer",
        lambda: SimpleNamespace(totals=lambda: {"tokens_total": 100}),
    )
    result = p.run()
    assert result.tripped is False, "预算内不应触发熔断"
    assert result.blocked is False


# ============================================================
# 7. --max-time=0 表示不限制
# ============================================================
def test_breaker_max_time_zero_means_unlimited(tmp_path: Path, monkeypatch) -> None:
    """--max-time=0（或 None）表示不限制墙钟。"""
    p = _make_pipeline(tmp_path, max_time=0)
    p._start_time = time.monotonic() - 1000  # 极大时间差
    monkeypatch.setattr(
        "agent.core.llmops.trace.get_tracer",
        lambda: SimpleNamespace(totals=lambda: {"tokens_total": 100}),
    )
    assert p._check_budget("test") is False, "max_time=0 时不应触发墙钟熔断"


# ============================================================
# 8. budget_margin 放大预算上限
# ============================================================
def test_breaker_budget_margin_allows_more(tmp_path: Path, monkeypatch) -> None:
    """budget_margin=2.0 时，预算上限翻倍，原超限 token 不再触发熔断。"""
    p = _make_pipeline(tmp_path, budget_margin=2.0)
    p._start_time = time.monotonic()
    # balanced 12 章基线 high=640k，margin=2.0 → 上限 1_280_000；1M 在预算内
    monkeypatch.setattr(
        "agent.core.llmops.trace.get_tracer",
        lambda: SimpleNamespace(totals=lambda: {"tokens_total": 1_000_000}),
    )
    assert p._check_budget("test") is False, "budget_margin=2.0 时 1M token 应在预算内"


def test_breaker_budget_margin_1_triggers(tmp_path: Path, monkeypatch) -> None:
    """budget_margin=1.0 时，1M token 超过 640k 上限触发熔断。"""
    p = _make_pipeline(tmp_path)
    p._start_time = time.monotonic()
    monkeypatch.setattr(
        "agent.core.llmops.trace.get_tracer",
        lambda: SimpleNamespace(totals=lambda: {"tokens_total": 1_000_000}),
    )
    assert p._check_budget("test") is True, "budget_margin=1.0 时 1M token 应超限"


# ============================================================
# 9. 单元层：规划阶段熔断（_check_budget 直接驱动 _autoplan_full_book）
# ============================================================
def test_breaker_planning_phase_trip_sets_blocked_and_tripped(tmp_path: Path, monkeypatch) -> None:
    """规划阶段 _check_budget 超限：_plan_tripped/_plan_blocked 置位，run() 返回 blocked+tripped。

    这里不隔离 _autoplan_full_book，而是用 monkeypatch 让首个 _check_budget 立即返回 True，
    使规划在第一个检查点即熔断、不会真正调用真实 M1~M4。
    """
    p = _make_pipeline(tmp_path)
    p._start_time = time.monotonic()
    # 让 _check_budget 在第一次调用即熔断（规划第一步后）
    call = {"n": 0}

    def _fake_check_budget(step: str) -> bool:
        call["n"] += 1
        return True

    monkeypatch.setattr(p, "_check_budget", _fake_check_budget)
    monkeypatch.setattr(
        "agent.core.llmops.trace.get_tracer",
        lambda: SimpleNamespace(totals=lambda: {"tokens_total": 100}),
    )
    result = p.run()
    assert result.tripped is True, "规划阶段熔断应置 result.tripped"
    assert result.blocked is True, "规划阶段熔断应置 result.blocked"
    assert call["n"] >= 1
