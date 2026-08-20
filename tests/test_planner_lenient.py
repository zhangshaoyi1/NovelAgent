"""Planner 结构化输出宽松重建回归测试（修复 bug1）

验证 ``PlannerAgent._build_plan_lenient`` 在结构化输出字段缺失/类型不符时，
仍能保留有效部分，而非整体退化为空计划。
"""

from __future__ import annotations

from pathlib import Path

from agent.agents.planner_agent import PlannerAgent


def test_build_plan_lenient_keeps_valid_parts(tmp_path: Path) -> None:
    agent = PlannerAgent(tmp_path)
    raw = {
        "brief": "逆袭人生",
        "title": "进度条人生",
        "genre": "modern",
        "total_chapters": 12,
        "episode_tree": [
            {"id": "A1", "name": "主线", "chapter_start": 1, "chapter_end": 12, "goal": "成长"},
        ],
        "character_skeleton": [
            {"name": "林默", "role": "主角", "arc": "从顽皮到坚韧"},  # 合法
            {"role": "缺名字的角色"},  # 缺 name → 应被丢弃
        ],
        "foreshadow_plan": [
            {"id": "F1", "content": "芯片真相"},  # 合法（其余字段有默认）
        ],
        "quality_targets": {"coherence": 82},  # 部分字段，其余取默认
    }
    result = agent._build_plan_lenient(raw, "逆袭人生")
    plan = result["plan"]

    assert plan.brief == "逆袭人生"
    assert plan.title == "进度条人生"
    assert plan.genre == "modern"
    assert plan.total_chapters == 12
    assert len(plan.episode_tree) == 1
    # 非法角色（缺 name）被丢弃，合法角色保留
    names = [c.name for c in plan.character_skeleton]
    assert names == ["林默"]
    # 合法伏笔保留
    assert len(plan.foreshadow_plan) == 1
    # 部分质量目标保留，其余默认
    assert plan.quality_targets.coherence == 82
    assert plan.quality_targets.foreshadow_recycle_rate == 0.90
    # G4: 丢弃字段清单（含校验失败的角色）
    assert len(result["discarded_characters"]) == 1
    assert "缺名字的角色" in result["discarded_characters"][0]


def test_build_plan_lenient_handles_non_dict(tmp_path: Path) -> None:
    agent = PlannerAgent(tmp_path)
    result = agent._build_plan_lenient(None, "兜底")
    plan = result["plan"]
    assert plan.brief == "兜底"
    assert plan.total_chapters == 100  # 默认
