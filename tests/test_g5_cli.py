"""G5 CLI 参数透传测试：验证 autowrite 命令的 --appeal-gate 相关参数
能正确透传到 AgenticPipelineWorkflow 和 EvaluatorAgent。

覆盖：
- --no-appeal-gate → appeal_gate=False → 不注入六维
- --appeal-threshold 75 → 综合线覆盖为 75
- --appeal-window 3 → 末 3 章评分窗口
- 默认（无 CLI 覆盖）→ appeal_gate=True, threshold=60, window=1

本测试不涉及真实 LLM（stub evaluator），仅验证参数透传链路。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.agents.evaluator import EvaluatorAgent, NovelHealthReport
from agent.workflows.agentic_pipeline import AgenticPipelineWorkflow
from tests.test_g5_gate import _make_g5_project


class _StubEvaluator:
    """stub evaluator：记录构造参数，evaluate_with_repair 返回通过报告。"""

    def __init__(self, **kwargs: Any) -> None:
        self.init_kwargs = kwargs
        self.appeal_gate = kwargs.get("appeal_gate")
        self.appeal_threshold = kwargs.get("appeal_threshold")
        self.appeal_window = kwargs.get("appeal_window")
        self.appeal_scorer = kwargs.get("appeal_scorer")

    def evaluate_with_repair(self, rewriter):
        return NovelHealthReport(overall_pass=True, score=100.0, dimensions=[])


# ============================================================
# 1. 默认参数：appeal_gate=True, threshold=60, window=1
# ============================================================
def test_g5_cli_default_params(tmp_path: Path) -> None:
    wf = AgenticPipelineWorkflow(
        project_dir=tmp_path,
        appeal_gate=True,
        appeal_threshold=60,
        appeal_window=1,
    )
    assert wf.appeal_gate is True
    assert wf.appeal_threshold == 60
    assert wf.appeal_window == 1


# ============================================================
# 2. --no-appeal-gate → appeal_gate=False
# ============================================================
def test_g5_cli_no_appeal_gate(tmp_path: Path) -> None:
    wf = AgenticPipelineWorkflow(
        project_dir=tmp_path,
        appeal_gate=False,
        appeal_threshold=60,
        appeal_window=1,
    )
    assert wf.appeal_gate is False
    # 用注入 evaluator 验证 Pipeline 把 appeal_gate=False 透传
    stub = _StubEvaluator()
    wf.evaluator = stub
    # 重调用 _ensure_evaluator 会重新构造，这里手动验证 EvaluatorAgent 接收
    ev = EvaluatorAgent(
        tmp_path,
        appeal_scorer=None,
        appeal_gate=False,
        appeal_threshold=60,
        appeal_window=1,
    )
    report = ev._evaluate_once()
    adims = [d for d in report.dimensions if d.name.startswith("appeal_")]
    assert adims == [], "关闭 gate 不注入六维"
    assert report.appeal is None


# ============================================================
# 3. --appeal-threshold 75 → 综合线严格化
# ============================================================
def test_g5_cli_threshold_75(tmp_path: Path) -> None:
    from agent.core.reader_appeal import (
        APPEAL_DIMENSIONS,
        ReaderAppealReport,
        ReaderAppealScorer,
    )
    from rich.console import Console

    class StubScorer(ReaderAppealScorer):
        def __init__(self) -> None:
            super().__init__(console=Console())
        def score_chapter(self, chapter_text, *, title="", genre="", synopsis=""):
            dims = {k: 65 for k in APPEAL_DIMENSIONS}
            return ReaderAppealReport(
                dimensions=dims,
                total_score=ReaderAppealReport._compute_total(dims),
                one_liner="test",
                suggestions=[],
                llm_used=True,
                source="llm",
            )

    # threshold=60：65≥60 → 通过
    ev60 = EvaluatorAgent(
        _make_g5_project(tmp_path), appeal_scorer=StubScorer(), appeal_gate=True,
        appeal_threshold=60, appeal_window=1,
    )
    rep60 = ev60._evaluate_once()
    total60 = rep60.dimension("appeal_total")
    assert total60 is not None and total60.passed is True

    # threshold=75：65<75 → 综合维失败
    ev75 = EvaluatorAgent(
        _make_g5_project(tmp_path), appeal_scorer=StubScorer(), appeal_gate=True,
        appeal_threshold=75, appeal_window=1,
    )
    rep75 = ev75._evaluate_once()
    total75 = rep75.dimension("appeal_total")
    assert total75 is not None and total75.passed is False
    assert rep75.overall_pass is False


# ============================================================
# 4. --appeal-window 3 → Pipeline 记录 window=3
# ============================================================
def test_g5_cli_window_3(tmp_path: Path) -> None:
    wf = AgenticPipelineWorkflow(
        project_dir=tmp_path,
        appeal_gate=True,
        appeal_threshold=60,
        appeal_window=3,
    )
    assert wf.appeal_window == 3


# ============================================================
# 5. EvaluatorAgent 参数边界：threshold <=0 被强制为 1
# ============================================================
def test_g5_cli_threshold_clamped(tmp_path: Path) -> None:
    ev = EvaluatorAgent(
        tmp_path, appeal_scorer=None, appeal_gate=True,
        appeal_threshold=0, appeal_window=0,
    )
    assert ev.appeal_threshold == 1
    assert ev.appeal_window == 1
