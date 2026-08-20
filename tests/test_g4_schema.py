"""G4 Schema 强校验测试（P0-2 验收）：验证 Planner 分级校验策略。

纯离线：用 fake decide 函数控制 chat_structured 返回值，验证 _validate_masterplan
的分级行为（关键字段硬拒重试 → 安全降级；非关键字段按条目降级）。
覆盖 PRD §7 验收②：结构化残缺输入被强校验拦截或安全降级。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from rich.console import Console

from agent.agents.planner_agent import (
    Arc,
    CharacterSketch,
    MasterPlan,
    PlannedForeshadow,
    PlannerAgent,
    QualityTargets,
)


# ============================================================
# 辅助：构造 PlannerAgent + fake decide
# ============================================================
def _make_planner(
    tmp_path: Path,
    decide_fn: Any = None,
    decide_async_fn: Any = None,
) -> PlannerAgent:
    """构造 PlannerAgent（离线模式，不使用真实 LLM）。"""
    return PlannerAgent(
        project_dir=tmp_path,
        llm_client=None,
        console=Console(quiet=True),
        decide=decide_fn,
        decide_async=decide_async_fn,
    )


def _valid_plan_dict() -> dict[str, Any]:
    """返回合法完整 MasterPlan dict（所有关键字段齐全）。"""
    return {
        "brief": "测试创作思路",
        "genre": "xiuxian",
        "title": "测试标题",
        "total_chapters": 12,
        "episode_tree": [
            {
                "id": "arc_1",
                "name": "第一卷",
                "chapter_start": 1,
                "chapter_end": 12,
                "goal": "变强",
                "subline_id": "",
            }
        ],
        "character_skeleton": [
            {
                "name": "林逸",
                "role": "protagonist",
                "faction": "青云宗",
                "realm": "凡间",
                "arc": "从废柴到巅峰",
                "fingerprint": "我命由我不由天",
            }
        ],
        "foreshadow_plan": [
            {
                "id": "F-01",
                "content": "神秘传承",
                "plant_at_est": 1,
                "expected_resolve_est": 10,
                "related_characters": ["林逸"],
            }
        ],
        "quality_targets": {
            "character_stability_high": 0,
            "setting_consistency_high": 0,
            "foreshadow_recycle_rate": 0.90,
            "coherence": 85,
            "readability": 80,
            "pacing_abnormal": 0.03,
            "logic_holes": 0,
        },
        "notes": "测试备注",
    }


# ============================================================
# 1. 合法 MasterPlan → _validate_masterplan() 返回完整 plan
# ============================================================
def test_validate_masterplan_valid(tmp_path: Path) -> None:
    """合法完整 plan → _validate_masterplan 返回完整 MasterPlan，无降级。"""
    plan_data = _valid_plan_dict()

    call_count = {"n": 0}

    def decide(messages):
        call_count["n"] += 1
        return dict(plan_data)

    planner = _make_planner(tmp_path, decide_fn=decide)
    plan = planner.run("测试创作思路")

    assert isinstance(plan, MasterPlan)
    assert plan.brief == "测试创作思路"
    assert plan.title == "测试标题"
    assert plan.total_chapters == 12
    assert len(plan.episode_tree) == 1
    assert len(plan.character_skeleton) == 1
    assert plan.character_skeleton[0].name == "林逸"
    assert planner._schema_degraded is False, "合法 plan 不应标记 schema_degraded"


# ============================================================
# 2. 关键字段缺失 → 重试 2 次，成功后返回完整 plan
# ============================================================
def test_validate_masterplan_missing_critical_retry(tmp_path: Path) -> None:
    """关键字段缺失 → 重试 2 次后成功 → 返回完整 plan，_schema_degraded=False。"""
    good_data = _valid_plan_dict()
    bad_data = dict(good_data)
    del bad_data["episode_tree"]  # 缺关键字段

    call_count = {"n": 0}

    def decide(messages):
        call_count["n"] += 1
        # 第一次返回缺 episode_tree 的坏数据，之后返回好数据
        if call_count["n"] == 1:
            return dict(bad_data)
        return dict(good_data)

    planner = _make_planner(tmp_path, decide_fn=decide)
    plan = planner.run("测试创作思路")

    assert isinstance(plan, MasterPlan)
    assert len(plan.episode_tree) == 1, "重试后应拿到完整 plan"
    assert planner._schema_degraded is False, "重试成功后不应标记 schema_degraded"
    # 至少调用了 2 次（首次坏数据 + 至少一次重试）
    assert call_count["n"] >= 2, f"关键字段缺失应触发重试，实际调用 {call_count['n']} 次"


# ============================================================
# 3. 关键字段重试耗尽 → 安全降级为 MasterPlan(brief=...)，_schema_degraded=True
# ============================================================
def test_validate_masterplan_critical_exhausted_degrade(tmp_path: Path) -> None:
    """关键字段重试耗尽 → 安全降级为 MasterPlan(brief)，_schema_degraded=True。"""
    bad_data = _valid_plan_dict()
    del bad_data["episode_tree"]  # 始终缺 episode_tree

    def decide(messages):
        return dict(bad_data)  # 始终返回坏数据

    planner = _make_planner(tmp_path, decide_fn=decide)
    plan = planner.run("测试创作思路")

    assert isinstance(plan, MasterPlan)
    assert plan.brief == "测试创作思路"
    # 关键字段缺失重试耗尽 → 安全降级（episode_tree 为空，非关键字段也空）
    assert len(plan.episode_tree) == 0, "降级后 episode_tree 应为空"
    assert planner._schema_degraded is True, "重试耗尽应标记 _schema_degraded=True"


def test_validate_masterplan_critical_exhausted_does_not_raise(tmp_path: Path) -> None:
    """关键字段重试耗尽 → 不 raise，run() 正常返回。"""
    bad_data = _valid_plan_dict()
    del bad_data["title"]  # 缺关键字段 title

    def decide(messages):
        return dict(bad_data)

    planner = _make_planner(tmp_path, decide_fn=decide)
    # 不应抛出任何异常
    plan = planner.run("测试创作思路")
    assert plan is not None
    assert planner._schema_degraded is True


# ============================================================
# 4. 非关键字段（角色缺 name）→ 按条目降级，不阻断，返回完整 plan
# ============================================================
def test_validate_masterplan_non_critical_degrade(tmp_path: Path) -> None:
    """非关键字段（角色缺 name）→ 该条目丢弃，其余保留，返回完整 plan。"""
    data = _valid_plan_dict()
    # 在 character_skeleton 中插入一条缺 name 的坏条目
    data["character_skeleton"].append({"role": "antagonist"})  # 缺 name

    def decide(messages):
        return dict(data)

    planner = _make_planner(tmp_path, decide_fn=decide)
    plan = planner.run("测试创作思路")

    assert isinstance(plan, MasterPlan)
    # 好条目保留
    assert len(plan.character_skeleton) == 1, "坏条目应被丢弃，好条目保留"
    assert plan.character_skeleton[0].name == "林逸"
    # 非关键降级不阻断，关键字段仍存在
    assert len(plan.episode_tree) == 1
    assert planner._schema_degraded is False, "非关键字段降级不应标记 schema_degraded"


def test_validate_masterplan_non_critical_foreshadow_degrade(tmp_path: Path) -> None:
    """非关键字段（伏笔缺 id）→ 该条目丢弃，其余保留。"""
    data = _valid_plan_dict()
    data["foreshadow_plan"].append({"content": "坏伏笔"})  # 缺 id

    def decide(messages):
        return dict(data)

    planner = _make_planner(tmp_path, decide_fn=decide)
    plan = planner.run("测试创作思路")

    assert len(plan.foreshadow_plan) == 1, "坏伏笔条目应被丢弃"
    assert plan.foreshadow_plan[0].id == "F-01"


# ============================================================
# 5. _build_plan_lenient() 返回 dict 结构
# ============================================================
def test_build_plan_lenient_returns_dict(tmp_path: Path) -> None:
    """_build_plan_lenient 返回 {"plan": ..., "discarded_characters": [...], "discarded_foreshadows": [...]}。"""
    planner = _make_planner(tmp_path)

    # 合法数据
    result = planner._build_plan_lenient(_valid_plan_dict(), "测试")
    assert isinstance(result, dict), "_build_plan_lenient 应返回 dict"
    assert "plan" in result, "返回值应含 'plan' 键"
    assert "discarded_characters" in result, "返回值应含 'discarded_characters' 键"
    assert "discarded_foreshadows" in result, "返回值应含 'discarded_foreshadows' 键"
    assert isinstance(result["plan"], MasterPlan)
    assert isinstance(result["discarded_characters"], list)
    assert isinstance(result["discarded_foreshadows"], list)


def test_build_plan_lenient_discards_bad_characters(tmp_path: Path) -> None:
    """_build_plan_lenient 丢弃缺 name 的角色并记录。"""
    planner = _make_planner(tmp_path)

    data = _valid_plan_dict()
    data["character_skeleton"].append({"role": "bad"})  # 缺 name

    result = planner._build_plan_lenient(data, "测试")
    assert len(result["discarded_characters"]) >= 1, "应记录至少 1 个丢弃角色"
    assert len(result["plan"].character_skeleton) == 1, "好角色保留"


def test_build_plan_lenient_discards_bad_foreshadows(tmp_path: Path) -> None:
    """_build_plan_lenient 丢弃缺 id 的伏笔并记录。"""
    planner = _make_planner(tmp_path)

    data = _valid_plan_dict()
    data["foreshadow_plan"].append({"content": "bad"})  # 缺 id

    result = planner._build_plan_lenient(data, "测试")
    assert len(result["discarded_foreshadows"]) >= 1, "应记录至少 1 个丢弃伏笔"
    assert len(result["plan"].foreshadow_plan) == 1, "好伏笔保留"


def test_build_plan_lenient_non_dict_returns_empty(tmp_path: Path) -> None:
    """_build_plan_lenient 对非 dict 输入返回空结构。"""
    planner = _make_planner(tmp_path)

    result = planner._build_plan_lenient("not a dict", "测试")
    assert isinstance(result, dict)
    assert result["discarded_characters"] == []
    assert result["discarded_foreshadows"] == []
    assert isinstance(result["plan"], MasterPlan)


# ============================================================
# 6. _extract_missing_critical_fields 静态方法
# ============================================================
def test_extract_missing_critical_fields_empty_for_non_validation_error() -> None:
    """非 ValidationError 输入 → 返回空列表。"""
    result = PlannerAgent._extract_missing_critical_fields(ValueError("test"))
    assert result == []


def test_extract_missing_critical_fields_extracts_critical() -> None:
    """ValidationError 包含关键字段类型非法 → 返回字段名列表。

    注：brief/genre/title/total_chapters/episode_tree 在 MasterPlan 中均有默认值，
    单纯“缺失”不会触发 ValidationError；此处用“类型非法”触发真实错误以验证提取逻辑。
    """
    try:
        # total_chapters/ episode_tree 类型非法 → 真实 ValidationError
        MasterPlan(
            brief="x",
            genre="xiuxian",
            title="t",
            total_chapters="not_int",  # 类型非法（应为 int）
            episode_tree="bad",  # 类型非法（应为 list[Arc]）
        )
    except ValidationError as e:
        result = PlannerAgent._extract_missing_critical_fields(e)
        assert "total_chapters" in result
        assert "episode_tree" in result
        # brief/genre/title 合法，不应在缺失列表中
        assert "brief" not in result
        assert "genre" not in result
        assert "title" not in result
    else:
        pytest.fail("类型非法的 MasterPlan 应抛出 ValidationError")


def test_extract_missing_critical_fields_excludes_non_critical() -> None:
    """ValidationError 仅含非关键字段（如 character_skeleton 条目）→ 返回空列表。"""
    try:
        MasterPlan(
            brief="x",
            genre="xiuxian",
            title="t",
            total_chapters=1,
            episode_tree=[],
            character_skeleton=[{"role": "bad"}],  # 缺 name，非关键
        )
    except ValidationError as e:
        result = PlannerAgent._extract_missing_critical_fields(e)
        assert result == [], f"非关键字段缺失不应列入关键列表，实际：{result}"
    else:
        pytest.fail("含缺 name 的 character_skeleton 应抛出 ValidationError")


# ============================================================
# 7. _retry_with_hint 返回 dict
# ============================================================
def test_retry_with_hint_returns_dict(tmp_path: Path) -> None:
    """_retry_with_hint 成功时返回 dict。"""
    good_data = _valid_plan_dict()

    def decide(messages):
        return dict(good_data)

    planner = _make_planner(tmp_path, decide_fn=decide)
    result = planner._retry_with_hint([{"role": "user", "content": "test"}], "hint")
    assert isinstance(result, dict)
    assert result.get("title") == "测试标题"


# ============================================================
# 8. run() 全流程：合法 plan 落盘 + Memory 回写
# ============================================================
def test_run_saves_plan_and_writes_memory(tmp_path: Path) -> None:
    """run() 成功时落盘 plan.json 并回写 Memory。"""
    from tests._g3_fakes import _StubMemory

    planner = _make_planner(tmp_path, decide_fn=lambda m: dict(_valid_plan_dict()))
    planner.memory = _StubMemory()
    plan = planner.run("测试创作思路")

    assert (tmp_path / ".state" / "plan.json").exists(), "run() 应落盘 plan.json"
    assert plan.brief == "测试创作思路"