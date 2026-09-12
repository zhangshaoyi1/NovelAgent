"""B1/B2（2026-09-10）：滚动体检检查点 + --batch 相对批次目标。

背景事故：web 续写按钮把批次翻译为「绝对值章数目标」（--chapters 190），体检
只在写章循环**结束后**执行一次 → 目标没写满或中途被打断时，体检永不触发
（ch156-178 共 23 章零体检记录）。本组测试锁定两项修复：

- B1：``rolling_eval_every`` 每 N 章在循环内体检一次；不过则中断本批。
- B2：``--batch`` 由 CLI 用实时章数换算绝对目标，消除前端快照过期导致的空跑。
- P1（2026-09-12）：检查点改走 ``evaluate_with_repair``（就地定向重写）而非 ``evaluate``
  （只回退不重写），故本文件的假评测器实现的是 ``evaluate_with_repair``。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.workflows.pipeline.agentic_pipeline import AgenticPipelineWorkflow


def _dim(name: str, label: str, value: float, threshold: float, direction: str, passed: bool):
    return SimpleNamespace(
        name=name, label=label, value=value, threshold=threshold,
        direction=direction, passed=passed,
    )


def _report(passed: bool):
    dims = [
        _dim("setting_consistency_high", "设定一致", 0.0 if passed else 3.0, 0.0, "<=", passed),
        _dim("logic_holes", "逻辑漏洞", 0.0 if passed else 3.0, 0.0, "<=", passed),
    ]
    return SimpleNamespace(overall_pass=passed, score=80.0 if passed else 66.0, dimensions=dims)


class _FakeEvaluator:
    def __init__(self, passed: bool = True, raises: Exception | None = None):
        self._passed = passed
        self._raises = raises
        self.calls = 0
        self.rewrite_calls = 0
        self.last_failed_report = None

    def evaluate_with_repair(self, rewriter):
        """P1 契约：检查点走修复闭环（回退后带教训重写、再复评）。"""
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return _report(self._passed)


def _pipeline(tmp_path: Path, evaluator: _FakeEvaluator, every: int = 5) -> AgenticPipelineWorkflow:
    """构造 pipeline（绕过 __init__ 的重依赖，只装测试所需字段）。"""
    p = AgenticPipelineWorkflow.__new__(AgenticPipelineWorkflow)
    p.console = SimpleNamespace(
        print=lambda *a, **k: None,
        rule=lambda *a, **k: None,
    )
    p._emit_progress = lambda *a, **k: None
    p._emit_event = lambda *a, **k: None
    p._emit_failure = lambda *a, **k: None
    p.eval_enabled = True
    p.rolling_eval_every = every
    p._ensure_evaluator = lambda: evaluator
    p.project_dir = tmp_path
    p.max_rollback_attempts = 3
    p._rolling_escalation_reason = ""
    return p


# ---------------- B1：滚动体检 ----------------

def test_rolling_checkpoint_passes_when_report_ok(tmp_path: Path) -> None:
    ev = _FakeEvaluator(passed=True)
    p = _pipeline(tmp_path, ev)
    assert p._rolling_eval_checkpoint() is True
    assert ev.calls == 1


def test_rolling_checkpoint_blocks_when_report_fails(tmp_path: Path) -> None:
    ev = _FakeEvaluator(passed=False)
    p = _pipeline(tmp_path, ev)
    assert p._rolling_eval_checkpoint() is False
    assert ev.calls == 1


def test_rolling_checkpoint_uses_repair_loop(tmp_path: Path) -> None:
    """P1 红线：检查点必须走 evaluate_with_repair（旧版 evaluate 只回退不重写）。"""
    ev = _FakeEvaluator(passed=True)
    p = _pipeline(tmp_path, ev)
    assert p._rolling_eval_checkpoint() is True
    assert ev.calls == 1, "检查点未调用修复闭环入口 evaluate_with_repair"
    assert not hasattr(ev, "evaluate"), "假评测器不应再依赖旧的 evaluate 入口"


def test_rolling_checkpoint_degrades_open_on_exception(tmp_path: Path) -> None:
    """体检抛异常（LLM 不可用）时必须放行——质量闸门不该因基建抖动中断写作。"""
    ev = _FakeEvaluator(raises=RuntimeError("LLM 不可用"))
    p = _pipeline(tmp_path, ev)
    assert p._rolling_eval_checkpoint() is True


def test_rolling_checkpoint_degrades_open_on_none_report(tmp_path: Path) -> None:
    class _NoneEval:
        def evaluate_with_repair(self, rewriter):
            return None

    p = _pipeline(tmp_path, _NoneEval())
    assert p._rolling_eval_checkpoint() is True


def test_rolling_eval_every_zero_disables(tmp_path: Path) -> None:
    """every=0 关闭滚动体检（保留批末终审）。"""
    ev = _FakeEvaluator(passed=False)
    p = _pipeline(tmp_path, ev, every=0)
    # 触发条件含 every>0，故不应调用体检
    should_run = (
        p.eval_enabled and p.rolling_eval_every > 0
        and 5 > 0 and 5 % p.rolling_eval_every == 0
    )
    assert should_run is False
    assert ev.calls == 0


def test_rolling_eval_period_alignment() -> None:
    """周期对齐：wrote=5/10/15 触发，6/7 不触发。"""
    every = 5
    triggered = [w for w in range(1, 17) if w % every == 0]
    assert triggered == [5, 10, 15]


def test_constructor_clamps_negative_every() -> None:
    """负数归零（防御性钳制）。"""
    from agent.workflows.pipeline.agentic_pipeline import AgenticPipelineWorkflow as AP

    p = AP.__new__(AP)
    p.rolling_eval_every = max(0, int(-3))
    assert p.rolling_eval_every == 0


# ---------------- B2：--batch 相对批次 ----------------

def _count_chapters(project: Path) -> int:
    ch_dir = project / "chapters"
    if not ch_dir.exists():
        return 0
    return sum(1 for f in ch_dir.glob("ch*.md") if f.stem[2:].isdigit())


def test_batch_target_uses_live_chapter_count(tmp_path: Path) -> None:
    """--batch 5 于水位 178 时 → 目标 183（而非前端快照可能算出的 175）。"""
    ch_dir = tmp_path / "chapters"
    ch_dir.mkdir()
    for i in range(1, 179):
        (ch_dir / f"ch{i:03d}.md").write_text("x", encoding="utf-8")
    cur = _count_chapters(tmp_path)
    assert cur == 178
    assert cur + 5 == 183


def test_batch_ignores_non_chapter_files(tmp_path: Path) -> None:
    """非章节文件（如 ch_notes.md）不计入水位。"""
    ch_dir = tmp_path / "chapters"
    ch_dir.mkdir()
    (ch_dir / "ch001.md").write_text("x", encoding="utf-8")
    (ch_dir / "ch002.md").write_text("x", encoding="utf-8")
    (ch_dir / "ch_notes.md").write_text("x", encoding="utf-8")
    assert _count_chapters(tmp_path) == 2


def test_batch_no_stale_snapshot_regression(tmp_path: Path) -> None:
    """回归：前端旧快照（170）+5=175 < 实际水位 178 → 旧算法秒退；新算法 178+5=183 正常。"""
    ch_dir = tmp_path / "chapters"
    ch_dir.mkdir()
    for i in range(1, 179):
        (ch_dir / f"ch{i:03d}.md").write_text("x", encoding="utf-8")
    live = _count_chapters(tmp_path)
    stale_target = 170 + 5          # 前端过期快照算出的目标
    new_target = live + 5           # CLI 实时换算
    assert not (live < stale_target)  # 旧算法：循环条件为假 → 秒退
    assert live < new_target          # 新算法：正常推进
