"""HA-Eval L4 · 处置策略层回归测试

替代 ``evaluator.py`` 里按 name prefix 硬编码的三处特判（golden_* / mainline_* /
ending_*），改为按维度属性（置信度 / 作用域 / 量纲 / 是否硬指标）声明式匹配。

锁死的核心不变式：
    **证据不可信（confidence=0）⇒ 只复评，绝不删章重写。**
这条直接对应 2026-09-08 事故——单条可疑分数触发了不可逆回滚。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent.agents.evaluator_types import DimensionResult
from agent.core.quality.disposition import (
    IRREVERSIBLE_ACTIONS,
    Action,
    DispositionGate,
    DispositionPlan,
    DispositionPolicy,
)
from agent.core.quality.eval_evidence import EvalEvidence


def _dim(name: str, *, value: float = 1.0, threshold: float = 0.0,
         direction: str = "<=", required: bool = True,
         confidence: float = 1.0) -> DimensionResult:
    ev = EvalEvidence()
    if confidence <= 0.0:
        ev.degrade("test")
    return DimensionResult(
        name, name, value, threshold, direction, required, "llm/default", evidence=ev
    )


def _golden_dim(key: str = "hook_strength") -> DimensionResult:
    return DimensionResult(
        f"golden_{key}", f"金三·{key}", 20.0, 40.0, ">=", False, "llm",
        evidence=EvalEvidence(),
    )


def _mainline_dim() -> DimensionResult:
    return DimensionResult(
        "mainline_progress", "主线推进", 1.0, 3.0, ">=", False, "computed"
    )


def _ending_dim() -> DimensionResult:
    return DimensionResult(
        "ending_convergence", "结局收敛", 0.2, 0.90, ">=", False, "computed"
    )


# ============================================================
# 1. 规则映射
# ============================================================
class TestRuleMapping:
    def test_untrusted_evidence_forces_retry(self):
        """事故护栏：不可信证据 ⇒ RETRY_EVAL，绝不 ROLLBACK_REWRITE。"""
        dims = [
            _dim("character_stability_high", value=3.0, confidence=0.0),
            _dim("logic_holes", value=3.0, confidence=0.0),
            _dim("coherence", value=3.0, threshold=85.0, direction=">=",
                 required=False, confidence=0.0),
        ]
        plan = DispositionPolicy().plan(dims)
        assert plan.action is Action.RETRY_EVAL
        assert "untrusted_evidence" in plan.rule_names

    def test_first_chapters_escalates(self):
        plan = DispositionPolicy().plan([_golden_dim()])
        assert plan.action is Action.ESCALATE
        assert "first_chapters_scope" in plan.rule_names

    def test_book_ending_escalates(self):
        for d in (_mainline_dim(), _ending_dim()):
            plan = DispositionPolicy().plan([d])
            assert plan.action is Action.ESCALATE
            assert "book_ending_scope" in plan.rule_names

    def test_hard_gate_rolls_back(self):
        plan = DispositionPolicy().plan([_dim("logic_holes", value=2.0)])
        assert plan.action is Action.ROLLBACK_REWRITE
        assert plan.requires_double_evidence is True

    def test_soft_score_local_repair(self):
        d = DimensionResult("coherence", "连贯性", 70.0, 85.0, ">=", False, "llm",
                            evidence=EvalEvidence())
        plan = DispositionPolicy().plan([d])
        assert plan.action is Action.LOCAL_REPAIR
        assert plan.is_irreversible is False

    def test_unregistered_ratio_falls_back_to_rollback(self):
        """未登记的 RATIO 软维 → 兜底规则，保持旧语义（回滚重写）。"""
        d = DimensionResult("some_ratio", "某比例", 0.1, 0.9, ">=", False, "computed")
        plan = DispositionPolicy().plan([d])
        assert plan.action is Action.ROLLBACK_REWRITE
        assert "fallback" in plan.rule_names

    def test_empty_failure_continues(self):
        assert DispositionPolicy().plan([]).action is Action.CONTINUE


# ============================================================
# 2. 优先级：越"不敢动"越优先
# ============================================================
class TestPrecedence:
    def test_retry_beats_everything(self):
        """不可信证据 + 开头问题 → 先复评（因为开头失败也可能是假的）。"""
        plan = DispositionPolicy().plan([
            _golden_dim(),
            _dim("logic_holes", value=1.0, confidence=0.0),
        ])
        assert plan.action is Action.RETRY_EVAL

    def test_escalate_beats_rollback(self):
        """开头问题 + 硬指标 → 上报人工（回滚修不到开头）。"""
        plan = DispositionPolicy().plan([
            _golden_dim(),
            _dim("logic_holes", value=2.0),
        ])
        assert plan.action is Action.ESCALATE

    def test_rollback_beats_local_repair(self):
        """硬指标 + 软评分维 → 回滚重写（一次覆盖两类问题）。"""
        plan = DispositionPolicy().plan([
            DimensionResult("coherence", "连贯性", 70.0, 85.0, ">=", False, "llm"),
            _dim("logic_holes", value=2.0),
        ])
        assert plan.action is Action.ROLLBACK_REWRITE


# ============================================================
# 3. 守门器四道检查
# ============================================================
class TestDispositionGate:
    def _plan(self) -> DispositionPlan:
        return DispositionPolicy().plan([_dim("logic_holes", value=2.0)])

    def test_allows_when_trusted_and_within_budget(self):
        auth = DispositionGate().authorize(self._plan(), chapters=5,
                                           budget_remaining=10_000_000)
        assert auth.ok is True
        assert auth.estimated_chapters == 5

    def test_rejects_untrusted(self):
        dims = [_dim("logic_holes", value=2.0, confidence=0.0)]
        plan = DispositionPlan(Action.ROLLBACK_REWRITE, "x", dims=dims)
        auth = DispositionGate().authorize(plan, chapters=5)
        assert auth.ok is False
        assert "不可信" in auth.reason

    def test_rejects_when_cost_exceeds_rule_cap(self):
        plan = self._plan()
        gate = DispositionGate(cost_per_chapter_tokens=1_000_000)
        auth = gate.authorize(plan, chapters=5)
        assert auth.ok is False
        assert "超出该动作上限" in auth.reason

    def test_rejects_when_over_budget(self):
        auth = DispositionGate().authorize(self._plan(), chapters=5,
                                           budget_remaining=100_000)
        assert auth.ok is False
        assert "超出剩余预算" in auth.reason

    def test_rejects_without_double_evidence_when_required(self):
        gate = DispositionGate(require_double_evidence=True)
        auth = gate.authorize(self._plan(), chapters=5, double_evidence=False)
        assert auth.ok is False
        assert "双证据" in auth.reason
        assert gate.authorize(self._plan(), chapters=5, double_evidence=True).ok is True

    def test_dry_run_returns_plan_without_authorizing(self):
        auth = DispositionGate().authorize(self._plan(), chapters=5, dry_run=True)
        assert auth.ok is False
        assert auth.dry_run is True
        assert "dry-run" in auth.reason
        assert auth.estimated_chapters == 5

    def test_only_rollback_is_irreversible(self):
        assert Action.ROLLBACK_REWRITE in IRREVERSIBLE_ACTIONS
        assert Action.LOCAL_REPAIR not in IRREVERSIBLE_ACTIONS


# ============================================================
# 4. 与 EvaluatorAgent 集成：不可信 ⇒ 零回滚
# ============================================================
class TestEvaluatorIntegration:
    def _make(self, tmp_path: Path, **kw: Any):
        from agent.agents.evaluator import EvaluatorAgent

        return EvaluatorAgent(tmp_path, rollback_window=5,
                              max_rollback_attempts=3, **kw)

    def test_untrusted_report_never_rolls_back(self, tmp_path: Path):
        """事故场景端到端：五维全 3.0 且不可信 → 零回滚、零重写。"""
        from agent.agents.evaluator_types import NovelHealthReport

        ev = self._make(tmp_path)
        rewrites: list[list[int]] = []
        rolls: list[Any] = []

        def fake_rollback(self, last_written=None):  # type: ignore[no-untyped-def]
            rolls.append(last_written)
            raise AssertionError("不可信证据不得触发回滚")

        rounds = {"n": 0}

        def fake_eval(self):  # type: ignore[no-untyped-def]
            rounds["n"] += 1
            return NovelHealthReport(
                overall_pass=False,
                dimensions=[
                    _dim("character_stability_high", value=3.0, confidence=0.0),
                    _dim("logic_holes", value=3.0, confidence=0.0),
                    DimensionResult("coherence", "连贯性", 3.0, 85.0, ">=",
                                    False, "llm/degraded",
                                    evidence=_dim("coherence", confidence=0.0).evidence),
                ],
            )

        ev._evaluate_once = fake_eval.__get__(ev, type(ev))  # type: ignore[method-assign]
        ev.trigger_rollback = fake_rollback.__get__(ev, type(ev))  # type: ignore[method-assign]

        report = ev.evaluate_with_repair(lambda ch: rewrites.append(list(ch)))

        assert rewrites == [], "不可信证据不得触发任何重写"
        assert rolls == [], "不可信证据不得触发任何回滚"
        assert report.escalated is True
        assert "不可信" in report.escalated_reason
        assert rounds["n"] == 2, "应复评一次后停止（不得无限循环）"

    def test_hard_gate_still_rolls_back_when_trusted(self, tmp_path: Path):
        """正常路径不受影响：硬指标失败且证据可信 → 照旧回滚重写。"""
        from agent.agents.evaluator_types import NovelHealthReport, RepairPlan

        ev = self._make(tmp_path)
        rewrites: list[list[int]] = []
        rounds = {"n": 0}

        def fake_eval(self):  # type: ignore[no-untyped-def]
            rounds["n"] += 1
            if rounds["n"] >= 2:
                return NovelHealthReport(overall_pass=True, dimensions=[])
            return NovelHealthReport(
                overall_pass=False,
                dimensions=[_dim("logic_holes", value=2.0, confidence=1.0)],
            )

        def fake_rollback(self, last_written=None):  # type: ignore[no-untyped-def]
            return RepairPlan(8, [8, 9, 10, 11, 12], "ok", rolled_back=True)

        ev._evaluate_once = fake_eval.__get__(ev, type(ev))  # type: ignore[method-assign]
        ev.trigger_rollback = fake_rollback.__get__(ev, type(ev))  # type: ignore[method-assign]

        report = ev.evaluate_with_repair(lambda ch: rewrites.append(list(ch)))

        assert report.overall_pass is True
        assert rewrites == [[8, 9, 10, 11, 12]]
        assert report.rollback_attempts == 1

    def test_dry_run_blocks_rollback(self, tmp_path: Path):
        from agent.agents.evaluator_types import NovelHealthReport

        ev = self._make(tmp_path, disposition_dry_run=True)
        rewrites: list[list[int]] = []

        def fake_eval(self):  # type: ignore[no-untyped-def]
            return NovelHealthReport(
                overall_pass=False,
                dimensions=[_dim("logic_holes", value=2.0)],
            )

        ev._evaluate_once = fake_eval.__get__(ev, type(ev))  # type: ignore[method-assign]

        report = ev.evaluate_with_repair(lambda ch: rewrites.append(list(ch)))
        assert rewrites == []
        assert report.escalated is True
        assert "dry-run" in report.escalated_reason

    def test_budget_guard_blocks_rollback(self, tmp_path: Path):
        from agent.agents.evaluator_types import NovelHealthReport

        ev = self._make(tmp_path, budget_remaining_tokens=1000)
        rewrites: list[list[int]] = []

        def fake_eval(self):  # type: ignore[no-untyped-def]
            return NovelHealthReport(
                overall_pass=False,
                dimensions=[_dim("logic_holes", value=2.0)],
            )

        ev._evaluate_once = fake_eval.__get__(ev, type(ev))  # type: ignore[method-assign]

        report = ev.evaluate_with_repair(lambda ch: rewrites.append(list(ch)))
        assert rewrites == []
        assert "剩余预算" in report.escalated_reason
