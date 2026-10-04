"""作者接受开篇（金三升级豁免）红线——实弹《凡尘炼废》p8 后新增。

守卫：
- **豁免语义**：``golden_three.opening_accepted=True`` 时，FIRST_CHAPTERS（金三）
  未达标维度从处置输入剔除 → 不再 escalate；豁免留痕进 report.notes
  （"豁免而非评分达标"）；其余维度照常裁决。
- **默认不豁免**：未接受时金三失败照常升级（守势缺省）。
- **CLI**：accept-opening 写 policy（带 note），--revoke 撤销。
- **范围**：豁免只作用于金三/FIRST_CHAPTERS 维度，非金三失败维度不得被豁免。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.agents.evaluator import EvaluatorAgent
from agent.core.quality.policy import golden_opening_accepted


def _dim(name: str, label: str, value: float, threshold: float,
         direction: str, required: bool):
    from agent.agents.evaluator import DimensionResult

    return DimensionResult(name, label, value, threshold, direction, required)


def _fail_report(golden_score: float = 54.0, threshold: float = 55.0):
    """金三未达标 + 一个普通硬指标失败的体检报告（验收线 55 已标定）。"""
    from agent.agents.evaluator import NovelHealthReport

    return NovelHealthReport(
        overall_pass=False,
        dimensions=[
            _dim("golden_total", "金三·综合", golden_score, threshold, ">=", True),
            _dim("character_stability_high", "人设稳定", 0.0, 0.0, "<=", True),
            _dim("logic_holes", "逻辑漏洞", 0.0, 0.0, "<=", True),
        ],
    )


def _accept(tmp_path: Path, note: str = "开篇慢热为有意节奏") -> None:
    p = tmp_path / ".state" / "quality_policy.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    policy = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    section = policy.get("golden_three") or {}
    section["opening_accepted"] = True
    section["opening_accepted_note"] = note
    policy["golden_three"] = section
    p.write_text(json.dumps(policy, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------- 豁免语义
def test_without_acceptance_golden_still_escalates(tmp_path) -> None:
    evalr = EvaluatorAgent(str(tmp_path), rollback_window=5, auto_rollback=True)
    report = _fail_report()
    failed = [d for d in report.dimensions if not d.passed]
    failed = evalr._apply_opening_acceptance(report, failed)
    assert any(d.name == "golden_total" for d in failed), "未接受时金三失败必须留在处置输入"
    decision = evalr._disposition.plan(failed)
    assert decision.action.value == "escalate"


def test_with_acceptance_golden_waived_but_hard_dims_remain(tmp_path) -> None:
    _accept(tmp_path)
    evalr = EvaluatorAgent(str(tmp_path), rollback_window=5, auto_rollback=True)
    report = _fail_report()
    failed = evalr._apply_opening_acceptance(
        report, [d for d in report.dimensions if not d.passed]
    )
    assert not any(d.name == "golden_total" for d in failed), "金三维度必须被豁免"
    assert not failed, "仅金三失败时豁免后应为空（其余维度均通过）"
    assert any("豁免而非评分达标" in n for n in report.notes), "豁免必须留痕（非达标）"


def test_accepted_evaluate_path_no_escalation(tmp_path) -> None:
    """端到端：接受开篇后 evaluate() 不再 escalate（p8 停批形态的直接回归）。"""
    _accept(tmp_path)
    evalr = EvaluatorAgent(str(tmp_path), rollback_window=5, auto_rollback=True)
    evalr._evaluate_once = lambda: _fail_report()  # type: ignore[method-assign]
    report = evalr.evaluate()
    assert not report.escalated
    assert any("作者已接受开篇" in n for n in report.notes)


def test_accepted_with_repair_path_closes_batch(tmp_path) -> None:
    """evaluate_with_repair：豁免后无其余失败 → 本批按作者接受收束（overall_pass）。"""
    from agent.agents.evaluator import NovelHealthReport

    _accept(tmp_path)
    evalr = EvaluatorAgent(str(tmp_path), rollback_window=5, auto_rollback=True)
    evalr._evaluate_once = lambda: _fail_report()  # type: ignore[method-assign]
    calls = {"rw": 0}

    def _rewriter(chapters):
        calls["rw"] += 1

    report = evalr.evaluate_with_repair(_rewriter)
    assert report.overall_pass is True
    assert calls["rw"] == 0, "豁免收束不得触发重写"
    assert not report.escalated


# ---------------------------------------------------------------- CLI
def test_accept_opening_writes_policy(tmp_path) -> None:
    from agent.cli.commands.accept_opening import accept_opening

    accept_opening(
        project_dir=str(tmp_path), json_output=True,
        note="作者读过 ch1-3，接受慢热开篇",
    )
    acc = golden_opening_accepted(tmp_path)
    assert acc["accepted"] is True
    assert "接受慢热开篇" in acc["note"]


def test_accept_opening_revoke(tmp_path) -> None:
    from agent.cli.commands.accept_opening import accept_opening

    accept_opening(project_dir=str(tmp_path), json_output=True, note="x")
    accept_opening(project_dir=str(tmp_path), json_output=True, revoke=True)
    assert golden_opening_accepted(tmp_path)["accepted"] is False
