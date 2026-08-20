"""G5 门禁测试（P0-1 验收）：迷爱看六维硬闸并入 EvaluatorAgent.overall_pass

覆盖：
- 低分（综合 < 60）→ 六维 passed=False → overall_pass=False（被拦截）
- 高分（综合 ≥ 60 且每维 ≥ 40）→ 六维 passed=True → overall_pass=True、零回溯
- 单维 < 40 但综合 ≥ 60 → 该维失败 → overall_pass=False（触底兜底）
- --appeal-threshold 覆盖综合线（75 更严；默认 60 该区间通过）
- --no-appeal-gate → 不注入六维、report.appeal=None、仅七维决定
- is_pass 门禁函数单测

纯离线：用 stub ReaderAppealScorer（仅覆写 score_chapter 返回预设报告），不碰 LLM。
"""

from __future__ import annotations

from pathlib import Path
from rich.console import Console

from agent.agents.evaluator_agent import EvaluatorAgent
from agent.core.reader_appeal import (
    APPEAL_DIMENSIONS,
    APPEAL_DIM_FLOOR,
    ReaderAppealReport,
    ReaderAppealScorer,
    is_pass,
)


class _StubAppealScorer(ReaderAppealScorer):
    """stub：score_chapter 返回预设报告，绝不触碰 LLM。"""

    def __init__(self, report: ReaderAppealReport) -> None:
        super().__init__(console=Console())
        self._report = report

    def score_chapter(self, chapter_text, *, title="", genre="", synopsis=""):
        return self._report


def _make_report(dims: dict[str, int], *, llm_used: bool = True) -> ReaderAppealReport:
    return ReaderAppealReport(
        dimensions=dims,
        total_score=ReaderAppealReport._compute_total(dims),
        one_liner="测试一句话感受",
        suggestions=["改进建议1"],
        llm_used=llm_used,
        source="offline" if not llm_used else "llm",
    )


def _appeal_dims(report) -> list:
    return [d for d in report.dimensions if d.name.startswith("appeal_")]


# ============================================================
# 1. 低分（综合 < 60）→ 六维全失败 → overall_pass=False
# ============================================================
def test_g5_gate_low_total_fails(tmp_path: Path) -> None:
    dims = {k: 30 for k in APPEAL_DIMENSIONS}  # 综合=30 < 60 且每维 < 40
    stub = _StubAppealScorer(_make_report(dims))
    ev = EvaluatorAgent(
        tmp_path, appeal_scorer=stub, appeal_gate=True,
        appeal_threshold=60, appeal_window=1,
    )
    report = ev._evaluate_once()
    adims = _appeal_dims(report)
    assert adims, "应注入六维 + 综合共 7 个 appeal_* DimensionResult"
    assert len(adims) == len(APPEAL_DIMENSIONS) + 1
    assert all(not d.passed for d in adims), "离线低分应全部判失败"
    assert report.overall_pass is False


# ============================================================
# 2. 高分（综合 ≥ 60 且每维 ≥ 40）→ 六维全通过 → overall_pass=True、零回溯
# ============================================================
def test_g5_gate_high_passes(tmp_path: Path) -> None:
    dims = {k: 80 for k in APPEAL_DIMENSIONS}  # 综合=80 ≥ 60 且每维 ≥ 40
    stub = _StubAppealScorer(_make_report(dims))
    ev = EvaluatorAgent(
        tmp_path, appeal_scorer=stub, appeal_gate=True, appeal_threshold=60,
    )
    report = ev._evaluate_once()
    adims = _appeal_dims(report)
    assert all(d.passed for d in adims), "高分应全部判通过"
    assert report.overall_pass is True
    assert report.appeal is not None
    assert report.appeal["passed"] is True


def test_g5_gate_high_no_rollback(tmp_path: Path) -> None:
    dims = {k: 80 for k in APPEAL_DIMENSIONS}
    stub = _StubAppealScorer(_make_report(dims))
    ev = EvaluatorAgent(tmp_path, appeal_scorer=stub, appeal_gate=True)
    called: list[list[int]] = []

    def rewriter(chapter_nums: list[int]) -> None:
        called.append(list(chapter_nums))

    report = ev.evaluate_with_repair(rewriter)
    assert report.overall_pass is True
    assert called == [], "高分不应触发回溯"
    assert report.rollback_attempts == 0


# ============================================================
# 3. 单维 < 40 但综合 ≥ 60 → 该维失败 → overall_pass=False（触底兜底）
# ============================================================
def test_g5_gate_single_dim_floor_triggers(tmp_path: Path) -> None:
    dims = {k: 80 for k in APPEAL_DIMENSIONS}
    dims["hook_strength"] = 30  # 某维 < 40；其余高 → 综合 ≈ 70 ≥ 60
    stub = _StubAppealScorer(_make_report(dims))
    ev = EvaluatorAgent(tmp_path, appeal_scorer=stub, appeal_gate=True)
    report = ev._evaluate_once()
    hook = report.dimension("appeal_hook_strength")
    assert hook is not None and hook.passed is False, "钩子强度 < 40 应判失败"
    assert report.overall_pass is False


# ============================================================
# 4. --appeal-threshold 覆盖综合线
# ============================================================
def test_g5_gate_threshold_override(tmp_path: Path) -> None:
    dims = {k: 65 for k in APPEAL_DIMENSIONS}  # 综合=65
    stub = _StubAppealScorer(_make_report(dims))
    # 默认 60：65 ≥ 60 → 通过
    ev_default = EvaluatorAgent(
        tmp_path, appeal_scorer=stub, appeal_gate=True, appeal_threshold=60,
    )
    rep_def = ev_default._evaluate_once()
    assert rep_def.overall_pass is True
    # 更严 75：65 < 75 → 综合维失败 → 不通过
    ev_strict = EvaluatorAgent(
        tmp_path, appeal_scorer=stub, appeal_gate=True, appeal_threshold=75,
    )
    rep_strict = ev_strict._evaluate_once()
    total_dim = rep_strict.dimension("appeal_total")
    assert total_dim is not None and total_dim.passed is False
    assert rep_strict.overall_pass is False


# ============================================================
# 5. --no-appeal-gate → 不注入六维、report.appeal=None、仅七维决定
# ============================================================
def test_g5_gate_disabled_no_appeal(tmp_path: Path) -> None:
    dims = {k: 0 for k in APPEAL_DIMENSIONS}  # 若门禁开必失败
    stub = _StubAppealScorer(_make_report(dims))
    ev = EvaluatorAgent(tmp_path, appeal_scorer=stub, appeal_gate=False)
    report = ev._evaluate_once()
    adims = _appeal_dims(report)
    assert adims == [], "关闭门禁不应注入六维 DimensionResult"
    assert report.appeal is None
    assert report.overall_pass is True  # 七维（无章节时全过）单独决定


# ============================================================
# 6. is_pass 门禁函数单测（T1 公共 API）
# ============================================================
def test_is_pass_function_low() -> None:
    low = _make_report({k: 30 for k in APPEAL_DIMENSIONS})
    passed, failed = is_pass(low, 60, APPEAL_DIM_FLOOR)
    assert passed is False
    assert len(failed) > 0


def test_is_pass_function_high() -> None:
    high = _make_report({k: 80 for k in APPEAL_DIMENSIONS})
    passed, failed = is_pass(high, 60, APPEAL_DIM_FLOOR)
    assert passed is True
    assert failed == []


def test_is_pass_function_single_floor() -> None:
    dims = {k: 80 for k in APPEAL_DIMENSIONS}
    dims["immersion"] = 35  # < 40 触底
    rep = _make_report(dims)
    passed, failed = is_pass(rep, 60, APPEAL_DIM_FLOOR)
    assert passed is False
    assert any("immersion" in f or "代入感" in f for f in failed)
