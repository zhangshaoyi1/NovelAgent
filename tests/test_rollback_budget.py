"""P1 回退预算 + 滚动体检「就地修复」回归测试（2026-09-12）。

背景：五灵破归档连续 31 次回退、同一章节窗口反复翻车，没有任何一条路径上报人工。
两个具体缺陷：

1. 滚动体检走 ``evaluate()``——**只回退、不重写**，修复被推给外层「再起一批盲写
   同样的 5 章」；
2. ``max_rollback_attempts`` 只活在单次 ``evaluate_with_repair`` 循环里，每批新建
   Evaluator 即归零，回退次数**不跨批**。

本测试把「跨批计数 → 连续超限 → escalated 上报人工」与「检查点必须走修复闭环」
钉成红线。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.core.quality.rollback_budget import RollbackBudget
from agent.workflows.pipeline.agentic_pipeline_agents import _PipelineAgentsMixin

# ---------------------------------------------------------------- 预算单元


def test_budget_counts_and_trips(tmp_path: Path) -> None:
    b = RollbackBudget.load(tmp_path, limit=3)
    assert b.consecutive == 0
    for expected in (1, 2, 3):
        assert b.bump(target_chapter=180, reason="硬指标不达标") == expected
    assert not b.tripped(), "等于上限不算熔断（> limit 才熔断）"
    assert b.bump(target_chapter=180, reason="硬指标不达标") == 4
    assert b.tripped(), "超过上限必须熔断"


def test_budget_reset_after_pass(tmp_path: Path) -> None:
    b = RollbackBudget.load(tmp_path, limit=3)
    b.bump(target_chapter=180, reason="x")
    b.bump(target_chapter=180, reason="x")
    b.reset()
    assert b.consecutive == 0
    assert b.total == 2, "累计次数保留，供复盘"
    assert not b.tripped()


def test_budget_tracks_same_target_churn(tmp_path: Path) -> None:
    """同一章节窗口反复回退（死循环特征）要能被识别。"""
    b = RollbackBudget.load(tmp_path, limit=5)
    b.bump(target_chapter=181, reason="x")
    b.bump(target_chapter=181, reason="x")
    b.bump(target_chapter=181, reason="x")
    assert b.same_target_streak == 3
    assert "同一章节窗口" in b.reason_text()
    b.bump(target_chapter=175, reason="x")
    assert b.same_target_streak == 1, "换窗口后重新计数"


def test_budget_persists_across_instances(tmp_path: Path) -> None:
    """跨批生效的关键：计数落盘，新实例（新进程）读得到。"""
    RollbackBudget.load(tmp_path, limit=3).bump(target_chapter=180, reason="批末体检")
    again = RollbackBudget.load(tmp_path, limit=3)
    assert again.consecutive == 1
    assert again.last_target == 180
    assert again.path.exists()


def test_budget_broken_file_degrades(tmp_path: Path) -> None:
    p = tmp_path / ".state" / "rollback_budget.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{不是合法 json", encoding="utf-8")
    b = RollbackBudget.load(tmp_path, limit=3)
    assert b.consecutive == 0 and not b.tripped(), "损坏文件按未计数处理，不抛异常"


# ---------------------------------------------------------------- 检查点接线


class _Dim:
    def __init__(self, label: str, value: float, passed: bool, required: bool = True) -> None:
        self.label, self.value, self.passed, self.required = label, value, passed, required


class _Plan:
    def __init__(self, target_chapter: int) -> None:
        self.target_chapter = target_chapter


class _Report:
    def __init__(self, gate: str, escalated: bool = False, target: int = 180) -> None:
        self.dimensions = [_Dim("连贯性", 35.0, gate == "pass")]
        self.score = 67.83
        self._gate = gate
        self.escalated = escalated
        self.escalated_reason = "评测器已放弃自动处置" if escalated else ""
        self.repair = _Plan(target)

    def gate_decision(self) -> str:
        return self._gate


class _Writer:
    def __init__(self, sink: list[Any]) -> None:
        self._sink = sink

    def run(self, rewrite_hint: str = "") -> None:
        self._sink.append(rewrite_hint)


class _Evaluator:
    def __init__(self, report: _Report, sink: list[Any]) -> None:
        self._report = report
        self._sink = sink
        self.last_failed_report = None

    def evaluate_with_repair(self, rewriter: Any) -> _Report:
        self._sink.append("evaluate_with_repair")
        rewriter([self._report.repair.target_chapter])  # 模拟评测器内部触发定向重写
        return self._report


class _FakePipeline(_PipelineAgentsMixin):
    def __init__(self, project_dir: Path, report: _Report, limit: int = 3) -> None:
        from rich.console import Console

        self.project_dir = project_dir
        self.console = Console()
        self.max_rollback_attempts = limit
        self._rolling_escalation_reason = ""
        self.calls: list[Any] = []
        self.failures: list[tuple[str, str, str]] = []
        self._report = report

    def _emit_progress(self, *a: Any, **k: Any) -> None:  # noqa: D102
        pass

    def _emit_event(self, *a: Any, **k: Any) -> None:  # noqa: D102
        pass

    def _emit_failure(self, kind: str, msg: str, severity: str = "warn") -> None:
        self.failures.append((kind, msg, severity))

    def _ensure_evaluator(self) -> Any:
        return _Evaluator(self._report, self.calls)

    def _ensure_writer(self) -> Any:
        return _Writer(self.calls)


def test_checkpoint_uses_repair_loop_not_plain_evaluate(tmp_path: Path) -> None:
    """检查点必须走 evaluate_with_repair（就地定向重写），不得只回退不重写。"""
    pipe = _FakePipeline(tmp_path, _Report("pass"))
    assert pipe._rolling_eval_checkpoint() is True
    assert "evaluate_with_repair" in pipe.calls, "检查点仍在用只回退不重写的 evaluate()"
    assert any(isinstance(c, str) and c != "evaluate_with_repair" for c in pipe.calls), (
        "重写回调未被调用 —— 修复闭环没接上"
    )


def test_checkpoint_pass_resets_budget(tmp_path: Path) -> None:
    RollbackBudget.load(tmp_path, limit=3).bump(target_chapter=180, reason="上一次")
    pipe = _FakePipeline(tmp_path, _Report("pass"))
    assert pipe._rolling_eval_checkpoint() is True
    assert RollbackBudget.load(tmp_path, limit=3).consecutive == 0


def test_checkpoint_block_breaks_batch_and_counts(tmp_path: Path) -> None:
    pipe = _FakePipeline(tmp_path, _Report("block"), limit=3)
    assert pipe._rolling_eval_checkpoint() is False, "不达标应中断本批"
    assert RollbackBudget.load(tmp_path, limit=3).consecutive == 1


def test_checkpoint_trips_and_escalates_after_limit(tmp_path: Path) -> None:
    """连续回退超上限 → 必须 escalated 上报人工，不再无限重试。"""
    pipe = _FakePipeline(tmp_path, _Report("block"), limit=3)
    for i in range(4):
        assert pipe._rolling_eval_checkpoint() is False, f"第 {i + 1} 次应停批"
    assert pipe._rolling_escalation_reason, "熔断后必须记录上报告知人工的原因"
    assert any(sev == "block" for _k, _m, sev in pipe.failures), "熔断应以 block 级上报"
    assert "超过上限 3" in pipe._rolling_escalation_reason


def test_checkpoint_propagates_evaluator_escalation(tmp_path: Path) -> None:
    """评测器自身已放弃（escalated）→ 检查点不得当作「再试一次」，直接上报人工。"""
    pipe = _FakePipeline(tmp_path, _Report("block", escalated=True), limit=9)
    assert pipe._rolling_eval_checkpoint() is False
    assert pipe._rolling_escalation_reason
    assert "评测器已放弃" in pipe._rolling_escalation_reason


def test_checkpoint_warn_soft_dimension_continues(tmp_path: Path) -> None:
    """软维度告警不得中断写作（HA-Eval L4 语义，防回归）。"""
    pipe = _FakePipeline(tmp_path, _Report("warn"), limit=3)
    assert pipe._rolling_eval_checkpoint() is True
