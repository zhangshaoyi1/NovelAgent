"""EvaluatorAgent.evaluate_with_repair 闭环回归测试（G1）

验证「不崩」第三层防御（自动回溯重写）的真实闭环：
- 不达标时触发回溯并调用 rewriter（带正确章节清单）；
- 限定次数内转达标 → overall_pass=True，rollback_attempts 正确；
- 超过 max_rollback_attempts 仍不达标 → escalated=True。

纯离线：不读真实章节、不调用 LLM，仅 monkeypatch 评测与回溯两个方法。
"""

from __future__ import annotations

import types
from pathlib import Path

from agent.agents.evaluator_agent import (
    DimensionResult,
    EvaluatorAgent,
    NovelHealthReport,
    RepairPlan,
)


def _dim(name: str, label: str, value: float, threshold: float,
         direction: str, required: bool) -> DimensionResult:
    return DimensionResult(name, label, value, threshold, direction, required)


def _fail_report() -> NovelHealthReport:
    """含硬指标 + 软指标失败的体检报告。"""
    return NovelHealthReport(
        overall_pass=False,
        dimensions=[
            _dim("character_stability_high", "人设稳定", 1.0, 0.0, "<=", True),
            _dim("setting_consistency_high", "设定一致", 0.0, 0.0, "<=", True),
            _dim("foreshadow_recycle_rate", "伏笔闭环", 0.50, 0.90, ">=", False),
            _dim("coherence", "连贯性", 60.0, 80.0, ">=", False),
            _dim("readability", "追读力", 50.0, 75.0, ">=", False),
            _dim("pacing_abnormal", "节奏异常", 0.0, 0.03, "<=", False),
            _dim("logic_holes", "逻辑漏洞", 0.0, 0.0, "<=", True),
        ],
    )


def _pass_report() -> NovelHealthReport:
    return NovelHealthReport(
        overall_pass=True,
        dimensions=[
            _dim("character_stability_high", "人设稳定", 0.0, 0.0, "<=", True),
            _dim("setting_consistency_high", "设定一致", 0.0, 0.0, "<=", True),
            _dim("foreshadow_recycle_rate", "伏笔闭环", 0.90, 0.90, ">=", False),
            _dim("coherence", "连贯性", 85.0, 80.0, ">=", False),
            _dim("readability", "追读力", 80.0, 75.0, ">=", False),
            _dim("pacing_abnormal", "节奏异常", 0.0, 0.03, "<=", False),
            _dim("logic_holes", "逻辑漏洞", 0.0, 0.0, "<=", True),
        ],
    )


def _fake_rollback(self, last_written=None) -> RepairPlan:
    return RepairPlan(
        target_chapter=8,
        chapters_to_rewrite=[8, 9, 10, 11, 12],
        reason="硬指标/总分不达标，回溯最近 5 章",
        rolled_back=True,
    )


def test_evaluate_with_repair_loops_then_passes(tmp_path: Path) -> None:
    ev = EvaluatorAgent(tmp_path, rollback_window=5, max_rollback_attempts=3)
    rounds = {"n": 0}
    chapters_seen: list[list[int]] = []

    def fake_eval_once(self) -> NovelHealthReport:
        rounds["n"] += 1
        return _fail_report() if rounds["n"] <= 2 else _pass_report()

    def rewriter(chapter_nums: list[int]) -> None:
        chapters_seen.append(list(chapter_nums))

    ev._evaluate_once = types.MethodType(fake_eval_once, ev)
    ev.trigger_rollback = types.MethodType(_fake_rollback, ev)

    report = ev.evaluate_with_repair(rewriter)

    assert report.overall_pass is True
    assert rounds["n"] == 3           # 2 次失败评测 + 1 次通过评测
    assert len(chapters_seen) == 2    # 回退并重写两轮
    assert chapters_seen[0] == [8, 9, 10, 11, 12]
    assert report.rollback_attempts == 2
    assert report.escalated is False


def test_evaluate_with_repair_escalates_after_max(tmp_path: Path) -> None:
    ev = EvaluatorAgent(tmp_path, rollback_window=5, max_rollback_attempts=3)
    rounds = {"n": 0}
    calls: list[list[int]] = []

    def fake_eval_once(self) -> NovelHealthReport:
        rounds["n"] += 1
        return _fail_report()

    def rewriter(chapter_nums: list[int]) -> None:
        calls.append(list(chapter_nums))

    ev._evaluate_once = types.MethodType(fake_eval_once, ev)
    ev.trigger_rollback = types.MethodType(_fake_rollback, ev)

    report = ev.evaluate_with_repair(rewriter)

    assert report.overall_pass is False
    assert report.escalated is True
    assert len(calls) == 3            # 达上限后停止
    assert report.rollback_attempts == 3


def test_evaluate_with_repair_no_rollback_when_pass(tmp_path: Path) -> None:
    ev = EvaluatorAgent(tmp_path, rollback_window=5, max_rollback_attempts=3)
    called: list[list[int]] = []

    def fake_eval_once(self) -> NovelHealthReport:
        return _pass_report()

    def rewriter(chapter_nums: list[int]) -> None:
        called.append(chapter_nums)

    ev._evaluate_once = types.MethodType(fake_eval_once, ev)

    report = ev.evaluate_with_repair(rewriter)
    assert report.overall_pass is True
    assert called == []               # 达标无需回溯
