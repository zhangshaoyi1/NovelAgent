"""G5 离线短路测试（修正点 A 验收）：
llm_used=False 时，六维每个 DimensionResult value=APPEAL_DIM_FLOOR(40)，
综合维 value=APPEAL_PASS_LINE(60)，全部 passed=True，禁止触发回溯。

同时验证：
- source="offline" 正确写入 appeal 子块
- overall_pass=True（无其他失败维度时）
- 不触发 evaluate_with_repair 的 rollback
- to_markdown 渲染降级提示
"""

from __future__ import annotations

from pathlib import Path
from rich.console import Console

from agent.agents.evaluator import EvaluatorAgent
from agent.core.reader_appeal import (
    APPEAL_DIMENSIONS,
    APPEAL_DIM_FLOOR,
    APPEAL_PASS_LINE,
    APPEAL_GATE_PREFIX,
    ReaderAppealReport,
    ReaderAppealScorer,
)
from tests.test_g5_gate import _make_g5_project


class _StubAppealScorerOffline(ReaderAppealScorer):
    """stub：返回 llm_used=False 的离线报告。"""

    def __init__(self) -> None:
        super().__init__(console=Console())

    def score_chapter(self, chapter_text, *, title="", genre="", synopsis=""):
        return ReaderAppealReport(
            dimensions={k: 0 for k in APPEAL_DIMENSIONS},
            total_score=0,
            one_liner="LLM 不可用",
            suggestions=[],
            llm_used=False,
            source="offline",
        )


class _StubAppealScorerOnline(ReaderAppealScorer):
    """stub：返回 llm_used=True 的高分在线报告。"""

    def __init__(self) -> None:
        super().__init__(console=Console())

    def score_chapter(self, chapter_text, *, title="", genre="", synopsis=""):
        dims = {k: 80 for k in APPEAL_DIMENSIONS}
        return ReaderAppealReport(
            dimensions=dims,
            total_score=ReaderAppealReport._compute_total(dims),
            one_liner="非常精彩",
            suggestions=[],
            llm_used=True,
            source="llm",
        )


# ============================================================
# 1. 离线短路：六维 value=40、综合=60、全部 passed=True
# ============================================================
def test_g5_offline_short_circuit_values(tmp_path: Path) -> None:
    stub = _StubAppealScorerOffline()
    ev = EvaluatorAgent(
        _make_g5_project(tmp_path), appeal_scorer=stub, appeal_gate=True,
        appeal_threshold=60, appeal_window=1,
        mainline_gate=False, ending_gate=False,  # G8（拍板 6）：G5 仅测六维
    )
    report = ev._evaluate_once()
    adims = [d for d in report.dimensions if d.name.startswith(APPEAL_GATE_PREFIX)]
    assert len(adims) == len(APPEAL_DIMENSIONS) + 1, "应注入六维 + 综合共 7 个"

    for d in adims:
        if d.name == "appeal_total":
            assert d.value == float(APPEAL_PASS_LINE), f"综合维应为 {APPEAL_PASS_LINE}"
            assert d.threshold == 60.0
        else:
            assert d.value == float(APPEAL_DIM_FLOOR), f"单维应为 {APPEAL_DIM_FLOOR}"
            assert d.threshold == float(APPEAL_DIM_FLOOR)
        assert d.passed is True, f"{d.name} 离线应强制通过"
        assert d.source == "offline"

    assert report.overall_pass is True, "离线短路不应导致 overall_pass=False"


# ============================================================
# 2. 离线短路：appeal 子块结构正确
# ============================================================
def test_g5_offline_appeal_subblock(tmp_path: Path) -> None:
    stub = _StubAppealScorerOffline()
    ev = EvaluatorAgent(_make_g5_project(tmp_path), appeal_scorer=stub, appeal_gate=True)
    report = ev._evaluate_once()
    assert report.appeal is not None
    a = report.appeal
    assert a["source"] == "offline"
    assert a["total_score"] == 0
    assert a["threshold"] == 60
    assert a["floor"] == APPEAL_DIM_FLOOR
    assert a["passed"] is True
    # dimensions 明细：离线时 score 为 0，但 floor 仍为 40
    for k in APPEAL_DIMENSIONS:
        assert a["dimensions"][k]["score"] == 0
        assert a["dimensions"][k]["floor"] == APPEAL_DIM_FLOOR


# ============================================================
# 3. 离线短路：evaluate_with_repair 不触发 rollback
# ============================================================
def test_g5_offline_no_rollback_triggered(tmp_path: Path) -> None:
    stub = _StubAppealScorerOffline()
    ev = EvaluatorAgent(
        _make_g5_project(tmp_path), appeal_scorer=stub, appeal_gate=True,
        mainline_gate=False, ending_gate=False,  # G8（拍板 6）：G5 仅测六维
    )
    called: list[list[int]] = []

    def rewriter(chapter_nums: list[int]) -> None:
        called.append(list(chapter_nums))

    report = ev.evaluate_with_repair(rewriter)
    assert report.overall_pass is True
    assert called == [], "离线短路不应触发回溯"
    assert report.rollback_attempts == 0
    assert report.escalated is False


# ============================================================
# 4. 离线短路：to_markdown 渲染降级提示
# ============================================================
def test_g5_offline_markdown_degraded(tmp_path: Path) -> None:
    stub = _StubAppealScorerOffline()
    ev = EvaluatorAgent(_make_g5_project(tmp_path), appeal_scorer=stub, appeal_gate=True)
    report = ev._evaluate_once()
    md = report.to_markdown()
    assert "迷爱看（离线通过，未实测）" in md
    assert "降级不阻断" in md or "LLM 不可用" in md


# ============================================================
# 5. 在线正常：与离线短路对比，确保在线不短路
# ============================================================
def test_g5_online_not_short_circuited(tmp_path: Path) -> None:
    stub = _StubAppealScorerOnline()
    ev = EvaluatorAgent(_make_g5_project(tmp_path), appeal_scorer=stub, appeal_gate=True)
    report = ev._evaluate_once()
    adims = [d for d in report.dimensions if d.name.startswith(APPEAL_GATE_PREFIX)]
    for d in adims:
        if d.name == "appeal_total":
            assert d.value == 80.0, "在线综合维应为真实分 80"
        else:
            assert d.value == 80.0, "在线单维应为真实分 80"
        assert d.source == "llm"
    assert report.appeal is not None
    assert report.appeal["source"] == "llm"
    md = report.to_markdown()
    assert "迷爱看（读者吸引力六维）" in md
    assert "离线通过，未实测" not in md


# ============================================================
# 6. 无 scorer 时 appeal_gate 开启但无 scorer → 不注入六维
# ============================================================
def test_g5_offline_no_scorer_means_no_appeal(tmp_path: Path) -> None:
    ev = EvaluatorAgent(tmp_path, appeal_scorer=None, appeal_gate=True)
    report = ev._evaluate_once()
    adims = [d for d in report.dimensions if d.name.startswith(APPEAL_GATE_PREFIX)]
    assert adims == [], "无 scorer 不应注入六维"
    assert report.appeal is None
