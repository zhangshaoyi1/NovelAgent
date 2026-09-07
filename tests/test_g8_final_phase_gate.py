"""G8 门禁结局窗口前置条件测试（2026-09-07 五灵破归档 20:58 轮假失败根治）。

背景：mainline_progress / ending_convergence 是全书收尾验收维度，旧实现
在每次 autowrite 结尾评估中无条件启用——1200 章的书写到第 16 章时，
支线 1/5（需≥3）、结局段在第 901 章尚未开始，两维必然假失败并 escalated
（exit 2），把一次完全成功的续写窗口判成全局结构事故。

修复：两维仅在 total_written >= ending_trigger(book_total, 0.25) 时启用；
book_total 未知时保守保持旧语义。
"""

from __future__ import annotations

from pathlib import Path

from agent.agents.evaluator import EvaluatorAgent
from agent.core.engine.state_machine import StateMachine
from tests.test_g8_mainline import _make_g8_project, S01


def _set_written(d: Path, n: int) -> None:
    sm = StateMachine(d)
    sm.load()
    sm.progress = {
        "current_subline": S01,
        "current_chapter": n,
        "total_written": n,
        "last_written_at": "",
    }
    sm.save()


def test_mid_book_window_skips_g8_dims(tmp_path: Path) -> None:
    """1200 章书写到 16 章（结局窗口前）→ G8 两维不参与评估，整体通过。"""
    d = _make_g8_project(
        tmp_path, n_sublines=5, target=1200,
        plan_json={"total_chapters": 1200, "subline_share": {}},
    )
    _set_written(d, 16)
    ev = EvaluatorAgent(d, mainline_gate=True, ending_gate=True)
    assert ev._in_book_final_phase() is False
    report = ev._evaluate_once()
    names = [x.name for x in report.dimensions]
    assert "mainline_progress" not in names
    assert "ending_convergence" not in names
    # G8 两维缺席 → 不可能因全局结构门禁 escalated（其余维失败与本测试无关）
    assert "全局结构门禁" not in (report.escalated_reason or "")


def test_final_phase_enables_g8_dims(tmp_path: Path) -> None:
    """写到第 901 章（1200×75%+1，结局窗口内）→ G8 两维恢复启用。"""
    d = _make_g8_project(
        tmp_path, n_sublines=5, target=1200,
        plan_json={"total_chapters": 1200, "subline_share": {}},
    )
    _set_written(d, 901)
    ev = EvaluatorAgent(d, mainline_gate=True, ending_gate=True)
    assert ev._in_book_final_phase() is True
    report = ev._evaluate_once()
    names = [x.name for x in report.dimensions]
    assert "ending_convergence" in names


def test_unknown_book_total_keeps_old_semantics(tmp_path: Path) -> None:
    """无 plan.json（book_total 未知）→ 保守保持旧语义，门禁启用。"""
    d = _make_g8_project(tmp_path, n_sublines=2, target=30, plan_json=None)
    _set_written(d, 5)
    plan_file = d / ".state" / "plan.json"
    if plan_file.exists():
        plan_file.unlink()
    ev = EvaluatorAgent(d, mainline_gate=True, ending_gate=True)
    assert ev._in_book_final_phase() is True


def test_short_book_reaching_ending_window(tmp_path: Path) -> None:
    """12 章书写到第 10 章（12×75%+1=10）→ 已进结局窗口，门禁启用。"""
    d = _make_g8_project(
        tmp_path, n_sublines=1, target=12,
        plan_json={"total_chapters": 12, "subline_share": {}},
    )
    _set_written(d, 10)
    ev = EvaluatorAgent(d, mainline_gate=True, ending_gate=True)
    assert ev._in_book_final_phase() is True
