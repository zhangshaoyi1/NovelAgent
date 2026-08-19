"""G2 收紧 LLM 质检维度判定 —— 阈值同步契约 + soft_margin 注入 + 门禁断言（P0-4 / P0-6）

纯离线：注入 stub score_fn，不调用真实 LLM。验证：
- P0-4 阈值两处同步（EvaluatorAgent.qt 默认 / QualityTargets 默认均为 85 / 80）。
- _SOFT_MARGIN 注入映射（硬门禁 0、coherence 5、其余 0）。
- DimensionResult.passed 在 soft_margin 下正确（边界噪声被吸收、劣质被抓、硬门禁不漏判）。
- P0-6 劣质样例 → overall_pass False；合格样例 → overall_pass True 且 rolled_back False。
- 覆盖「仅硬门禁触发 / 仅评分维触发 / 全部通过」三情形。
"""

from __future__ import annotations

from pathlib import Path

from agent.agents.evaluator_agent import DimensionResult, EvaluatorAgent, _SOFT_MARGIN
from agent.agents.planner_agent import QualityTargets


# ============================================================
# P0-4 阈值两处同步契约
# ============================================================
def test_threshold_sync_evaluator_default(tmp_path: Path) -> None:
    ev = EvaluatorAgent(tmp_path)
    assert ev.qt["coherence"] == 85.0
    assert ev.qt["readability"] == 80.0


def test_threshold_sync_planner_default() -> None:
    qt = QualityTargets()
    assert qt.coherence == 85.0
    assert qt.readability == 80.0


# ============================================================
# _SOFT_MARGIN 注入映射 + passed 判定
# ============================================================
def test_soft_margin_mapping() -> None:
    assert _SOFT_MARGIN["character_stability_high"] == 0.0
    assert _SOFT_MARGIN["setting_consistency_high"] == 0.0
    assert _SOFT_MARGIN["logic_holes"] == 0.0
    assert _SOFT_MARGIN["coherence"] == 5.0
    assert _SOFT_MARGIN["readability"] == 0.0
    assert _SOFT_MARGIN["foreshadow_recycle_rate"] == 0.0
    assert _SOFT_MARGIN["pacing_abnormal"] == 0.0


def test_soft_margin_passed_judgement() -> None:
    # coherence value=82, threshold=85, soft_margin=5 → 82 >= 80 → 通过（吸收边界噪声）
    dr = DimensionResult("coherence", "连贯性", 82.0, 85.0, ">=", False, soft_margin=5.0)
    assert dr.passed is True
    # coherence value=60, threshold=85, soft_margin=5 → 60 >= 80? 否 → 不通过（劣质被抓）
    dr_bad = DimensionResult("coherence", "连贯性", 60.0, 85.0, ">=", False, soft_margin=5.0)
    assert dr_bad.passed is False
    # 硬门禁 value=1 vs 0（soft_margin=0）→ 1 <= 0? 否 → 不通过（0 漏判）
    dr_hard_fail = DimensionResult(
        "character_stability_high", "人设稳定", 1.0, 0.0, "<=", True, soft_margin=0.0
    )
    assert dr_hard_fail.passed is False
    # 硬门禁 value=0 → 通过
    dr_hard_pass = DimensionResult(
        "character_stability_high", "人设稳定", 0.0, 0.0, "<=", True, soft_margin=0.0
    )
    assert dr_hard_pass.passed is True


# ============================================================
# P0-6 门禁断言（注入 stub score_fn，覆盖三情形）
# ============================================================
def _good_scores(name: str) -> float:
    """合格样例：计数维=0、coherence=90、readability=88；确定性维达标。"""
    return {
        "character_stability_high": 0.0,
        "setting_consistency_high": 0.0,
        "logic_holes": 0.0,
        "coherence": 90.0,
        "readability": 88.0,
        "foreshadow_recycle_rate": 1.0,
        "pacing_abnormal": 0.0,
    }[name]


def test_poor_sample_fails(tmp_path: Path) -> None:
    """劣质样例（character=2、logic=1、coherence=60、readability=50）→ overall_pass False。"""

    def score_fn(name: str, project_dir: str) -> float:
        return {
            "character_stability_high": 2.0,
            "setting_consistency_high": 0.0,
            "logic_holes": 1.0,
            "coherence": 60.0,
            "readability": 50.0,
            "foreshadow_recycle_rate": 1.0,
            "pacing_abnormal": 0.0,
        }[name]

    ev = EvaluatorAgent(tmp_path, score_fn=score_fn)
    report = ev._evaluate_once()
    assert report.overall_pass is False


def test_good_sample_passes_no_rollback(tmp_path: Path) -> None:
    """合格样例（计数维=0、coherence=90、readability=88）→ overall_pass True 且 rolled_back False。"""
    ev = EvaluatorAgent(tmp_path, score_fn=_good_scores)
    report = ev._evaluate_once()
    assert report.overall_pass is True
    assert report.rolled_back is False


def test_only_hard_gate_triggered(tmp_path: Path) -> None:
    """仅硬门禁触发（character=1，其余达标）：整体不通过，软评分维应通过。"""

    def score_fn(name: str, project_dir: str) -> float:
        return {
            "character_stability_high": 1.0,
            "setting_consistency_high": 0.0,
            "logic_holes": 0.0,
            "coherence": 90.0,
            "readability": 88.0,
            "foreshadow_recycle_rate": 1.0,
            "pacing_abnormal": 0.0,
        }[name]

    ev = EvaluatorAgent(tmp_path, score_fn=score_fn)
    report = ev._evaluate_once()
    assert report.overall_pass is False
    assert report.dimension("character_stability_high").passed is False
    assert report.dimension("coherence").passed is True


def test_only_score_dim_triggered(tmp_path: Path) -> None:
    """仅评分维触发（coherence=60，其余达标）：整体不通过，硬门禁应通过。"""

    def score_fn(name: str, project_dir: str) -> float:
        return {
            "character_stability_high": 0.0,
            "setting_consistency_high": 0.0,
            "logic_holes": 0.0,
            "coherence": 60.0,
            "readability": 88.0,
            "foreshadow_recycle_rate": 1.0,
            "pacing_abnormal": 0.0,
        }[name]

    ev = EvaluatorAgent(tmp_path, score_fn=score_fn)
    report = ev._evaluate_once()
    assert report.overall_pass is False
    assert report.dimension("coherence").passed is False
    assert report.dimension("character_stability_high").passed is True


def test_all_pass(tmp_path: Path) -> None:
    """全部通过：整体通过。"""
    ev = EvaluatorAgent(tmp_path, score_fn=_good_scores)
    report = ev._evaluate_once()
    assert report.overall_pass is True
