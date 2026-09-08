# -*- coding: utf-8 -*-
"""分线预算三道护栏回归（2026-09-08 五灵破归档实证修复）。

覆盖三项修正：
1. ``core/story/subline_curve`` —— 压力曲线随预算同步，解除 ``min(曲线上界, cap)``
   的单向钳制（此前预算扩张永远不兑现，形成棘轮效应）。
2. ``BudgetPlanner._protect_irreversible`` —— 已写支线的累计预算不得被压到当前
   章号以下，否则支线会在没走完高潮时被整体切走。
3. ``BudgetPlanner._route_anchor_lines`` —— prompt 注入主角路线节点作为对齐锚。
"""

from __future__ import annotations

import json

import pytest

from agent.core.story.subline_curve import (
    parse_curve_rows,
    rebuild_curve_section,
    sync_pressure_curves,
    sync_subline_curve,
)
from agent.workflows.pipeline.budget_planner import BudgetPlanner

CURVE_MD = """## 剧集压力曲线

| 阶段 | 章节 | 张力等级 |
|---|---|---|
| 铺垫 | 1-100 | 低 |
| 冲突 | 101-200 | 中 |
| 高潮 | 201-250 | 高 |
| 舒缓 | 251-300 | 低 |
"""


# ---------------------------------------------------------------- 曲线缩放
def test_parse_curve_rows():
    rows = parse_curve_rows(CURVE_MD)
    assert [r["stage"] for r in rows] == ["铺垫", "冲突", "高潮", "舒缓"]
    assert [r["lo"] for r in rows] == [1, 101, 201, 251]
    assert [r["hi"] for r in rows] == [100, 200, 250, 300]
    assert rows[2]["tension"] == "高"


def test_rebuild_keeps_proportions_and_aligns_end():
    """等比缩放：末段上界必须精确落在 end（吸收舍入残差）。"""
    new = rebuild_curve_section(CURVE_MD, 1, 600)
    rows = parse_curve_rows(new)
    assert [r["lo"] for r in rows] == [1, 201, 401, 501]
    assert [r["hi"] for r in rows] == [200, 400, 500, 600]
    # 张力等级与阶段名原样保留
    assert [r["tension"] for r in rows] == ["低", "中", "高", "低"]


def test_rebuild_shift_and_shrink():
    """区间平移 + 收缩：总长 300 → 100，四段仍单调相接、无空洞。"""
    new = rebuild_curve_section(CURVE_MD, 301, 400)
    rows = parse_curve_rows(new)
    assert rows[0]["lo"] == 301
    assert rows[-1]["hi"] == 400
    for a, b in zip(rows, rows[1:]):
        assert int(b["lo"]) == int(a["hi"]) + 1


def test_rebuild_returns_none_on_invalid_range():
    assert rebuild_curve_section(CURVE_MD, 0, 100) is None
    assert rebuild_curve_section(CURVE_MD, 200, 100) is None


def test_rebuild_returns_none_without_rows():
    assert rebuild_curve_section("（无表格）", 1, 100) is None


# ---------------------------------------------------------------- 曲线同步
def _make_project(tmp_path, shares: dict[str, int]):
    """建一个含 sublines 的最小项目，返回路径。"""
    sublines: list[str] = []
    for sid, _ in shares.items():
        d = tmp_path / "sublines" / sid
        d.mkdir(parents=True, exist_ok=True)
        (d / "subline.md").write_text(
            f"# {sid}\n\n{CURVE_MD}\n\n## 其它\n\n占位\n", encoding="utf-8"
        )
        sublines.append(sid)
    return tmp_path, sublines


def test_sync_pressure_curves_uses_cumulative_range(tmp_path):
    """份额换算成累计区间：S01=100 → 1-100，S02=50 → 101-150。"""
    shares = {"S01_a": 100, "S02_b": 50}
    tmp_path, sublines = _make_project(tmp_path, shares)
    notes = sync_pressure_curves(tmp_path, shares, sublines)
    assert len(notes) == 2

    r1 = parse_curve_rows(
        (tmp_path / "sublines" / "S01_a" / "subline.md").read_text(encoding="utf-8")
    )
    r2 = parse_curve_rows(
        (tmp_path / "sublines" / "S02_b" / "subline.md").read_text(encoding="utf-8")
    )
    assert (r1[0]["lo"], r1[-1]["hi"]) == (1, 100)
    assert (r2[0]["lo"], r2[-1]["hi"]) == (101, 150)
    assert "S02_b 压力曲线同步为 101-150" in notes


def test_sync_is_idempotent(tmp_path):
    """同一预算重复同步不产生变更说明。"""
    shares = {"S01_a": 100, "S02_b": 50}
    tmp_path, sublines = _make_project(tmp_path, shares)
    sync_pressure_curves(tmp_path, shares, sublines)
    assert sync_pressure_curves(tmp_path, shares, sublines) == []


def test_sync_expansion_now_takes_effect(tmp_path):
    """回归核心：预算扩张此前被静态曲线吞掉，同步后必须兑现。

    原区间 1-300；预算扩到 370 → 曲线应变为 1-370，否则 min() 会在 300 提前切线。
    """
    shares = {"S01_a": 370}
    tmp_path, sublines = _make_project(tmp_path, shares)
    sync_pressure_curves(tmp_path, shares, sublines)
    rows = parse_curve_rows(
        (tmp_path / "sublines" / "S01_a" / "subline.md").read_text(encoding="utf-8")
    )
    assert rows[-1]["hi"] == 370


def test_sync_single_missing_file_is_noop(tmp_path):
    assert sync_subline_curve(tmp_path, "SXX_missing", 1, 10) is None


def test_sync_other_sections_preserved(tmp_path):
    shares = {"S01_a": 200}
    tmp_path, sublines = _make_project(tmp_path, shares)
    sync_pressure_curves(tmp_path, shares, sublines)
    text = (tmp_path / "sublines" / "S01_a" / "subline.md").read_text(encoding="utf-8")
    assert "## 其它" in text
    assert "占位" in text


# ------------------------------------------------------- 不可逆保护
def _planner_with_progress(tmp_path, written: int, current: str) -> BudgetPlanner:
    st = tmp_path / ".state"
    st.mkdir(parents=True, exist_ok=True)
    (st / "state.json").write_text(
        json.dumps(
            {"progress": {"total_written": written, "current_subline": current}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return BudgetPlanner(project_dir=tmp_path)


def test_protect_raises_hold_subline_above_written(tmp_path):
    """已写 46 章而 S01 累计预算仅 20 → 必须抬升到 ≥47，否则下一裁决点立刻切线。"""
    pl = _planner_with_progress(tmp_path, 46, "S01_a")
    out = pl._protect_irreversible({"S01_a": 20, "S02_b": 200}, ["S01_a", "S02_b"])
    assert out["S01_a"] >= 47
    assert out["S01_a"] + out["S02_b"] == 220  # 总量守恒


def test_protect_noop_when_budget_sufficient(tmp_path):
    pl = _planner_with_progress(tmp_path, 46, "S01_a")
    share = {"S01_a": 300, "S02_b": 280}
    assert pl._protect_irreversible(share, ["S01_a", "S02_b"]) == share


def test_protect_targets_current_subline(tmp_path):
    """已写 500 章、当前在 S02，而前两条累计仅 300 → 抬升 S02 而非 S01。"""
    pl = _planner_with_progress(tmp_path, 500, "S02_b")
    out = pl._protect_irreversible(
        {"S01_a": 100, "S02_b": 200, "S03_c": 300}, ["S01_a", "S02_b", "S03_c"]
    )
    assert out["S01_a"] == 100  # 已写满的支线不动
    assert out["S01_a"] + out["S02_b"] >= 501


def test_protect_allows_overflow_when_cannot_compensate(tmp_path):
    """后续支线无章可扣时，宁可超出 horizon 也不能让当前支线被切。"""
    pl = _planner_with_progress(tmp_path, 900, "S02_b")
    out = pl._protect_irreversible({"S01_a": 100, "S02_b": 50}, ["S01_a", "S02_b"])
    assert out["S01_a"] + out["S02_b"] >= 901


def test_protect_skipped_without_progress(tmp_path):
    pl = BudgetPlanner(project_dir=tmp_path)
    share = {"S01_a": 10, "S02_b": 20}
    assert pl._protect_irreversible(share, ["S01_a", "S02_b"]) == share


# ------------------------------------------------------- 路线锚点
def test_route_anchor_lines_reads_plan(tmp_path):
    st = tmp_path / ".state"
    st.mkdir(parents=True, exist_ok=True)
    (st / "plan.json").write_text(
        json.dumps(
            {
                "total_chapters": 1200,
                "route": {
                    "nodes": [
                        {"id": "N01", "chapter_range": "1-200", "milestone": "解锁量产"},
                        {"id": "N04", "chapter_range": "601-800", "milestone": "过客危机"},
                        {"id": "N09", "chapter_range": ""},
                    ]
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    lines = BudgetPlanner(project_dir=tmp_path)._route_anchor_lines()
    assert lines == ["  - N01 1-200：解锁量产", "  - N04 601-800：过客危机"]


def test_route_anchor_lines_missing_plan(tmp_path):
    assert BudgetPlanner(project_dir=tmp_path)._route_anchor_lines() == []


def test_route_anchor_lines_breaks_on_corrupt_json(tmp_path):
    st = tmp_path / ".state"
    st.mkdir(parents=True, exist_ok=True)
    (st / "plan.json").write_text("{ not json", encoding="utf-8")
    assert BudgetPlanner(project_dir=tmp_path)._route_anchor_lines() == []


# ------------------------------------------------------- 重规划窗口
def test_replan_window_decoupled_from_mainline_window(tmp_path):
    """重规划窗口默认 25（原为推进窗口 5），可通过参数覆盖。"""
    from agent.workflows.pipeline.mainline_orchestrator import MainlineOrchestrator

    orch = MainlineOrchestrator(tmp_path, mainline_window=5)
    assert orch.replan_window == MainlineOrchestrator.DEFAULT_REPLAN_WINDOW == 25
    assert MainlineOrchestrator(tmp_path, mainline_window=5, replan_window=10).replan_window == 10


# ------------------------------------------------------- prompt 注入
def test_prompt_contains_route_anchor(tmp_path):
    st = tmp_path / ".state"
    st.mkdir(parents=True, exist_ok=True)
    (st / "plan.json").write_text(
        json.dumps(
            {"route": {"nodes": [{"id": "N01", "chapter_range": "1-200", "milestone": "解锁量产"}]}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    prompt = BudgetPlanner(project_dir=tmp_path)._build_user_prompt(
        ["S01_a"], 1200, {}
    )
    assert "主角路线节点" in prompt
    assert "N01 1-200：解锁量产" in prompt
    assert "不允许出现" in prompt  # 空档禁令


@pytest.mark.parametrize("written", [0, 1])
def test_protect_boundary_zero_and_one(tmp_path, written):
    """边界：written=0 跳过；written=1 且预算为 1 时累计 cap 必须 ≥2。"""
    pl = _planner_with_progress(tmp_path, written, "S01_a")
    share = {"S01_a": 1, "S02_b": 99}
    out = pl._protect_irreversible(share, ["S01_a", "S02_b"])
    if written == 0:
        assert out == share
    else:
        assert out["S01_a"] >= 2
