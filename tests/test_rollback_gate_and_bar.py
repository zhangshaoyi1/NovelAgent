"""回退门槛 + 回退前置闸（2026-09-17）

登记单：``项目文档/优化/20260917_回退熔断仅事后生效_计数维回退门槛不可达.md``

两条被钉住的契约
----------------
1. **回退门槛**（`DimensionSpec.rollback_min_value`）：LLM 计数维的轻越界（1–2 条）
   **不再授权整窗回退**，改走可逆的定向修复；达门槛（≥3 条）才允许销毁末窗。
   ⚠ 反向对账：**降低的是动作强度，不是质量要求** —— 阈值仍为 0、`required` 仍为
   True、该维照样判失败进报告。若有人为了"消灭回退"去动阈值/required，本文件会 FAIL。
2. **回退前置闸**（`EvaluatorAgent.rollback_barrier`）：跨批熔断（`RollbackBudget.tripped()`）
   原先只在 `evaluate_with_repair()` **返回之后**被上层读到 ⇒ 只能事后停批、拦不住
   已经发生的销毁。现改为在 `trigger_rollback()` 动手**之前**生效。

行为级断言（不只断言"函数被调用"）
--------------------------------
- 轻越界：**断言 rollback provider 一次都没被调用**（内容真的没被销毁），
  且 rewriter 被调用（走了可逆路径），且 `overall_pass` 仍为 False（判据未放行）。
- 达门槛：断言 rollback provider **真的**被调用，且 rewriter 拿到重写清单。
- 前置闸：断言 `tripped()` 为真时 provider 零调用 + 原因落到 `last_rollback_barrier_reason`。
"""

from __future__ import annotations

import inspect
import io
from pathlib import Path
from types import SimpleNamespace

from rich.console import Console

from agent.agents.evaluator import (
    DimensionResult,
    EvaluatorAgent,
    NovelHealthReport,
)
from agent.core.quality.dimension_registry import (
    DIMENSIONS,
    ROLLBACK_MIN_COUNT,
)
from agent.core.quality.disposition import Action

BAR_DIMS = ("character_stability_high", "setting_consistency_high")
_WINDOW = 5


def _quiet() -> Console:
    return Console(file=io.StringIO(), width=200)


def _dim(name: str, value: float) -> DimensionResult:
    spec = DIMENSIONS[name]
    return DimensionResult(
        name, spec.label, float(value), spec.default_threshold,
        spec.direction.value, spec.required, "llm/default",
    )


def _report(bar_dim: str, value: float) -> NovelHealthReport:
    """只让一个硬指标维失败，其余全部达标 —— 失败归因单一，断言不歧义。"""
    dims = [_dim("coherence", 95.0), _dim("readability", 90.0)]
    dims.append(_dim(bar_dim, value))
    return NovelHealthReport(overall_pass=False, dimensions=dims)


class _Harness:
    """驱动 `evaluate_with_repair` 的可观测替身：记录**真的**发生了哪些动作。"""

    def __init__(self, report: NovelHealthReport, *, barrier=None, attempts: int = 2) -> None:
        self.report = report
        self.rollback_calls: list[int] = []
        self.rewrite_calls: list[list[int]] = []
        self.ev = EvaluatorAgent(
            Path("."),
            console=_quiet(),
            max_rollback_attempts=attempts,
            rollback_barrier=barrier,
        )
        self.ev._evaluate_once = lambda: self.report  # type: ignore[method-assign]
        self.ev._last_written = lambda: 41  # type: ignore[method-assign]
        self.ev._resolve_rollback = lambda: SimpleNamespace(  # type: ignore[method-assign]
            rollback_to_chapter=self._rollback_to_chapter
        )

    def _rollback_to_chapter(self, target: int):
        self.rollback_calls.append(target)
        return SimpleNamespace(success=True)

    def _rewriter(self, chapters: list[int]) -> None:
        self.rewrite_calls.append(list(chapters))

    def run(self) -> NovelHealthReport:
        return self.ev.evaluate_with_repair(self._rewriter)


def test_below_bar_does_not_destroy_window() -> None:
    """轻越界（未达门槛）⇒ 零销毁；走可逆定向修复；判据不放行。"""
    for name in BAR_DIMS:
        h = _Harness(_report(name, 1.0))
        report = h.run()
        assert h.rollback_calls == [], (
            f"{name}=1.0（未达回退门槛 {ROLLBACK_MIN_COUNT}）不得销毁末窗，"
            f"实测回退了 {h.rollback_calls}"
        )
        assert h.rewrite_calls, f"{name}: 轻越界应走可逆定向修复（rewriter 未被调用）"
        assert report.overall_pass is False, (
            f"{name}: 判据不得被放宽 —— 轻越界仍是失败，只是不再销毁整窗"
        )


def test_at_or_above_bar_destroys_window() -> None:
    """达门槛 ⇒ 真的执行整窗回退（新口径没有把回退能力整体废掉）。"""
    for name in BAR_DIMS:
        for value in (ROLLBACK_MIN_COUNT, ROLLBACK_MIN_COUNT + 2):
            h = _Harness(_report(name, value))
            h.run()
            assert h.rollback_calls, (
                f"{name}={value}（≥ 门槛 {ROLLBACK_MIN_COUNT}）必须授权整窗回退，"
                f"实测零回退 ⇒ 判据可达时不得降级动作强度"
            )
            assert h.rewrite_calls, f"{name}={value}: 回退后必须重写窗口章节"


def test_barrier_blocks_rollback_before_action() -> None:
    """前置闸置位 ⇒ 连门都不进，且原因显性化。"""
    h = _Harness(
        _report("setting_consistency_high", ROLLBACK_MIN_COUNT + 1),
        barrier=lambda: (True, "连续回退 7 次，超过上限 3"),
    )
    report = h.run()
    assert h.rollback_calls == [], "熔断置位时不得再执行销毁动作"
    assert h.ev.last_rollback_barrier_reason, "被拦下的原因必须留存，供上报人工"
    assert report.escalated is True, "被前置闸拦下必须上报人工，不得静默继续"
    assert "前置闸" in (report.escalated_reason or ""), (
        f"escalated_reason 应指明是前置闸拦下，实测：{report.escalated_reason!r}"
    )


def test_barrier_exception_forbids_rollback() -> None:
    """闸门自身故障 ⇒ 按**禁止回退**处理（护栏失效不得静默删章）。"""
    def _boom():
        raise RuntimeError("budget unreadable")

    h = _Harness(_report("character_stability_high", ROLLBACK_MIN_COUNT + 1), barrier=_boom)
    h.run()
    assert h.rollback_calls == [], (
        "护栏读不到 ≠ 可以删章：闸门异常时必须保守拒绝回退"
    )


def test_barrier_absent_keeps_legacy_behaviour() -> None:
    """未注入闸门（例如单测/CLI 直用 Evaluator）⇒ 行为与旧版一致。

    未注入时回退循环仍由实例级 ``max_rollback_attempts`` 约束（本例 attempts=2 ⇒
    最多 2 次回退），这正是**注入闸门要补的那一环**：跨批状态不再只活在一次调用里。
    """
    h = _Harness(_report("character_stability_high", ROLLBACK_MIN_COUNT + 1), barrier=None)
    h.run()
    assert h.rollback_calls and set(h.rollback_calls) == {37}, (
        f"未注入闸门时不应改变旧行为（应仍按 max_rollback_attempts 回退），实测 {h.rollback_calls}"
    )
    assert len(h.rollback_calls) == 2, (
        f"旧行为应回退满 max_rollback_attempts=2 次，实测 {h.rollback_calls}"
    )


def test_pipeline_injects_barrier_reading_tripped() -> None:
    """接线对账：管线必须把「读 tripped() 的回调」注入 Evaluator。

    仅断言"某函数存在"是不够的 —— 必须同时证明它被**传给了 Evaluator**，
    否则闸门只是个死代码（正是本登记单要根治的「判据未成为前置约束」）。
    """
    import agent.workflows.pipeline.agentic_pipeline_agents as mod

    src = inspect.getsource(mod)
    assert "rollback_barrier=self._rollback_barrier_check" in src, (
        "管线未把前置闸注入 EvaluatorAgent ⇒ 闸门永不生效"
    )
    assert "budget.tripped()" in src, "前置闸必须读跨批熔断状态"
    assert "def _rollback_barrier_check" in src, "前置闸实现缺失"


def test_quality_bar_unchanged_while_action_lowered() -> None:
    """★ 反向对账：本次只降**动作强度**，未降**质量要求**。

    防止后来者用「把阈值抬高 / required 关掉」来让回退消失 —— 那会让硬指标
    静默变成软指标，与「质量优先不降档」冲突。
    """
    for name in BAR_DIMS:
        spec = DIMENSIONS[name]
        assert spec.required is True, f"{name}: required 必须仍为 True（硬指标不得转软）"
        assert spec.default_threshold == 0.0, f"{name}: 阈值 0 是报告线，不得抬高"
        assert spec.rollback_min_value == ROLLBACK_MIN_COUNT, (
            f"{name}: 回退门槛应为 {ROLLBACK_MIN_COUNT}，实测 {spec.rollback_min_value}"
        )


def test_below_bar_action_is_reversible_local_repair() -> None:
    """规则层对账：轻越界命中的是可逆规则，而不是"没命中任何规则"。"""
    from agent.core.quality.disposition import DispositionPolicy

    for name in BAR_DIMS:
        plan = DispositionPolicy().plan([_dim(name, 1.0)])
        assert plan.action is Action.LOCAL_REPAIR, (
            f"{name}: 轻越界应走 LOCAL_REPAIR，实测 {plan.action.value}"
        )
        assert "hard_gate_below_rollback_bar" in plan.rule_names, (
            f"{name}: 应由显式规则授权可逆动作，而不是落进兜底，实测规则 {plan.rule_names}"
        )
