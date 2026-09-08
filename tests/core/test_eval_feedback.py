"""体检反馈闭环测试（2026-09-08 缺口1/缺口2 修复）

缺口1：build_rewrite_hint 附带 EvalEvidence.issues 逐条明细（含不可信证据跳过）。
缺口2：eval_lessons 保存/加载 roundtrip（通过时清零、读失败返回空）。
"""

from __future__ import annotations

import json
import time

import pytest

from agent.agents.evaluator_types import DimensionResult, NovelHealthReport
from agent.core.quality.eval_evidence import EvalEvidence, build_evidence
from agent.core.quality.eval_lessons import (
    load_eval_lessons_text,
    save_eval_lessons,
)
from agent.workflows.pipeline.agentic_pipeline_types import build_rewrite_hint


def _dim(name: str, label: str, value: float, threshold: float,
         direction: str = "<=", evidence: EvalEvidence | None = None) -> DimensionResult:
    return DimensionResult(
        name, label, value, threshold, direction, True, "llm/default",
        evidence=evidence,
    )


# ---------------------------------------------------------------- 缺口 1

def test_rewrite_hint_includes_issue_details():
    """失败维度带可信证据时，hint 必须包含逐条问题明细。"""
    ev = build_evidence(
        messages=[{"role": "user", "content": "x"}],
        raw_response='{"value": 4, "issues": []}',
        issues=[
            {"type": "人设", "severity": "high", "desc": "赵无痕性格突变，前文谨慎此时鲁莽"},
            {"type": "人设", "severity": "mid", "desc": "林凡突然使用未解锁的灵技"},
        ],
        rationale="人设漂移明显",
    )
    dim = _dim("character_stability_high", "人设稳定", 4.0, 0.0, evidence=ev)
    report = NovelHealthReport(overall_pass=False, score=29.6, dimensions=[dim])

    hint = build_rewrite_hint(report, [37, 41])

    assert "赵无痕性格突变" in hint
    assert "林凡突然使用未解锁的灵技" in hint
    assert "评审理由：人设漂移明显" in hint
    assert "[high]" in hint  # severity 保留
    assert "第 37–41 章" in hint


def test_rewrite_hint_skips_degraded_evidence():
    """证据不可信（confidence=0，如缓存串值）时不输出明细，避免误导重写。"""
    ev = EvalEvidence(issues=[{"type": "人设", "severity": "high", "desc": "可疑问题"}],
                      rationale="可疑理由")
    ev.degrade("batch duplicate response")
    dim = _dim("character_stability_high", "人设稳定", 3.0, 0.0, evidence=ev)
    report = NovelHealthReport(overall_pass=False, score=29.6, dimensions=[dim])

    hint = build_rewrite_hint(report, [37])

    assert "可疑问题" not in hint
    assert "可疑理由" not in hint
    # 维度级汇总仍在
    assert "人设稳定（character_stability_high）" in hint


def test_rewrite_hint_backward_compatible_without_evidence():
    """无证据（computed 维/旧路径）时行为与旧版一致，不报错。"""
    dim = _dim("foreshadow_recycle_rate", "伏笔闭环", 0.5, 0.9, direction=">=")
    report = NovelHealthReport(overall_pass=False, score=40.0, dimensions=[dim])

    hint = build_rewrite_hint(report, [])

    assert "伏笔闭环（foreshadow_recycle_rate）" in hint
    assert "实测 0.5 ≥ 合格线 0.9" in hint


def test_rewrite_hint_includes_appeal_suggestions():
    """迷爱看子块的改进建议（最多 3 条）并入 hint。"""
    report = NovelHealthReport(overall_pass=False, score=42.0, dimensions=[])
    report.appeal = {
        "source": "llm", "total_score": 55, "threshold": 60,
        "suggestions": ["开场钩子前移", "减少环境描写的静态段落", "给配角加动机", "第四条不出现"],
    }

    hint = build_rewrite_hint(report, [1])

    assert "开场钩子前移" in hint
    assert "给配角加动机" in hint
    assert "第四条不出现" not in hint


def test_rewrite_hint_includes_computed_dim_details():
    """计算型维度（伏笔/节奏）的确定性 evidence 明细同样进入 hint。"""
    from agent.core.quality.eval_evidence import build_evidence as _be

    ev = _be(issues=[
        {"type": "伏笔", "severity": "mid", "desc": "伏笔 F-01「五行吞噬诀」预期回收 S03/E01/ch401 已到期未回收"},
    ], rationale="到期伏笔回收率 0.50")
    dim = _dim("foreshadow_recycle_rate", "伏笔闭环", 0.5, 0.9, direction=">=", evidence=ev)
    report = NovelHealthReport(overall_pass=False, score=40.0, dimensions=[dim])

    hint = build_rewrite_hint(report, [37])

    assert "伏笔 F-01「五行吞噬诀」" in hint
    assert "已到期未回收" in hint


def test_lessons_include_appeal_suggestions(tmp_path):
    """迷爱看/黄金三章建议落入教训文件并在加载时输出。"""
    dims = [_dim("appeal_total", "迷·综合", 55.0, 60.0, direction=">=")]
    report = NovelHealthReport(overall_pass=False, score=55.0, dimensions=dims)
    report.appeal = {
        "source": "llm", "total_score": 55, "threshold": 60,
        "suggestions": ["加强章末钩子", "提升爽点密度"],
    }
    report.golden_three = None

    save_eval_lessons(tmp_path, report)

    text = load_eval_lessons_text(tmp_path)
    assert "加强章末钩子" in text
    assert "评审建议" in text


# ---------------------------------------------------------------- 缺口 2

def test_lessons_roundtrip_fail(tmp_path):
    """体检失败 → 落盘 → 加载出人话教训段（含逐条问题）。"""
    ev = build_evidence(
        messages=[{"role": "user", "content": "x"}],
        raw_response="{}",
        issues=[{"type": "设定", "severity": "high", "desc": "灵剑广场位置与第12章矛盾"}],
        rationale="设定冲突",
    )
    dims = [
        _dim("setting_consistency_high", "设定一致", 3.0, 0.0, evidence=ev),
        _dim("coherence", "连贯性", 28.0, 85.0, direction=">="),  # 无证据
    ]
    report = NovelHealthReport(overall_pass=False, score=29.61, dimensions=dims)

    save_eval_lessons(tmp_path, report)

    path = tmp_path / ".state" / "memory" / "eval_lessons.json"
    assert path.exists()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["overall_pass"] is False
    assert len(raw["failures"]) == 2

    text = load_eval_lessons_text(tmp_path)
    assert "上一轮全书体检未通过" in text
    assert "灵剑广场位置与第12章矛盾" in text
    assert "【连贯性】" in text
    assert "整体自查该维度" in text  # 无证据维给出兜底指引


def test_lessons_cleared_on_pass(tmp_path):
    """体检通过 → 落盘空 failures → 加载返回空串（教训清零）。"""
    dims = [_dim("coherence", "连贯性", 88.0, 85.0, direction=">=")]
    report = NovelHealthReport(overall_pass=True, score=95.0, dimensions=dims)

    save_eval_lessons(tmp_path, report)

    assert load_eval_lessons_text(tmp_path) == ""


def test_lessons_missing_file_returns_empty(tmp_path):
    assert load_eval_lessons_text(tmp_path) == ""


def test_lessons_degraded_evidence_skipped(tmp_path):
    """不可信证据的问题明细不进教训（防串值误导后续新章）。"""
    ev = EvalEvidence(issues=[{"type": "人设", "severity": "high", "desc": "串值假问题"}])
    ev.degrade("cache collision")
    dims = [_dim("character_stability_high", "人设稳定", 3.0, 0.0, evidence=ev)]
    report = NovelHealthReport(overall_pass=False, score=29.0, dimensions=dims)

    save_eval_lessons(tmp_path, report)

    text = load_eval_lessons_text(tmp_path)
    assert "串值假问题" not in text
    assert "【人设稳定】" in text  # 维度级教训保留


def test_lessons_text_capped(tmp_path):
    """教训段超长时截断到 max_chars，防挤占正文预算。"""
    ev = build_evidence(
        messages=[{"role": "user", "content": "x"}],
        raw_response="{}",
        issues=[{"type": "逻辑", "severity": "high", "desc": "长" * 200} for _ in range(10)],
    )
    dims = [_dim("logic_holes", "逻辑漏洞", 10.0, 0.0, evidence=ev)]
    report = NovelHealthReport(overall_pass=False, score=10.0, dimensions=dims)

    save_eval_lessons(tmp_path, report)

    text = load_eval_lessons_text(tmp_path, max_chars=800)
    assert len(text) <= 800
