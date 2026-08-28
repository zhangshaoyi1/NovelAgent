"""T-5 ConsistencyChecker 数据化落地测试

覆盖：
- check() 不再抛 NotImplementedError，返回 ConsistencyReport
- assess_architecture_impact() 返回空壳报告（不再抛 NotImplementedError）
- 内置 field_conflict 规则委托 ConflictArbiter（mock 验证）
- 四个 post-write 真实规则：timeline_conflict / relation_conflict /
  golden_finger_overstep / realm_overstep
- to_markdown() 不再抛 NotImplementedError
"""
from __future__ import annotations

from pathlib import Path

from agent.core.quality.consistency_checker import (
    ConsistencyChecker,
    ConsistencyReport,
    CheckTrigger,
)


def _seed_project(project: Path) -> None:
    """构造最小可校验项目：角色档案 + 关系网 + 世界观境界体系。"""
    (project / "characters").mkdir(parents=True)
    # 周伯：后期才牺牲（当前应存活）——用于 timeline/relation 校验
    (project / "characters" / "周伯.md").write_text(
        "---\nname: \"周伯\"\n---\n# 角色档案\n## 内核\n"
        "- 弧光：终结状态：为保护线索而死，死前将尸语秘术传予李承安\n",
        encoding="utf-8",
    )
    # 李承安：禁用词含 系统、金手指——用于 golden_finger 校验
    (project / "characters" / "李承安.md").write_text(
        "---\nname: \"李承安\"\n---\n# 角色档案\n## 语言指纹\n- 禁用词：系统、金手指\n",
        encoding="utf-8",
    )
    (project / "relations").mkdir(parents=True)
    (project / "relations" / "graph.md").write_text(
        "# 关系网\n"
        "## 节点\n| ID | 角色 | 分组 |\n|---|---|---|\n"
        "| A | 李承安 | protagonist |\n| C | 周伯 | mentor |\n\n"
        "## 边（关系）\n"
        "| 起 | 止 | 类型 | 强度 | 起于 | 备注 |\n"
        "|---|---|---|---|---|---|\n"
        "| A | C | 和解 | 9 | ch164 | 周伯相助 |\n",
        encoding="utf-8",
    )
    (project / "world.md").write_text(
        "## 世界观\n境界：凡人 < 练气 < 筑基\n", encoding="utf-8"
    )


def test_check_no_longer_raises(tmp_path: Path) -> None:
    """check() 不再抛 NotImplementedError，无设定变更且无章节时无冲突（passed=True）"""
    checker = ConsistencyChecker(project_dir=tmp_path)
    report = checker.check(CheckTrigger.PRE_WRITE, ctx={})
    assert isinstance(report, ConsistencyReport)
    assert report.passed is True
    assert report.conflicts == []


def test_assess_architecture_impact_returns_report(tmp_path: Path) -> None:
    """架构影响评估返回空壳报告"""
    checker = ConsistencyChecker(project_dir=tmp_path)
    report = checker.assess_architecture_impact()
    assert isinstance(report, ConsistencyReport)
    assert report.conflicts == []
    assert report.passed is True


def test_field_conflict_delegates_to_arbiter(tmp_path: Path) -> None:
    """field_conflict 规则委托 ConflictArbiter.check_new_setting 收集冲突"""

    class _FakeConflict:
        is_block = True
        description = "主角属性与世界观冲突"
        affected_chapters = ["ch3"]
        suggestions = ["调整设定"]

    class _FakeReport:
        conflicts = [_FakeConflict()]

    class _FakeArbiter:
        def check_new_setting(self, new_setting, subline_id=None):
            return _FakeReport()

    checker = ConsistencyChecker(project_dir=tmp_path)
    checker._arbiter = _FakeArbiter()
    report = checker.check(
        CheckTrigger.PRE_UPDATE_SETTING,
        ctx={"new_setting": "主角改为水属性灵根"},
    )
    assert any(c.rule_id == "field_conflict" for c in report.conflicts)
    assert report.passed is False


def test_timeline_conflict_catches_dead_character(tmp_path: Path) -> None:
    """POST_WRITE：角色档案为后期才牺牲，本章却称其已故 → BLOCK"""
    _seed_project(tmp_path)
    checker = ConsistencyChecker(tmp_path)
    report = checker.check(
        CheckTrigger.POST_WRITE,
        ctx={"chapter_text": "周伯在十年前便已经故去，尸骨早已凉透。"},
    )
    assert any(c.rule_id == "timeline_conflict" for c in report.conflicts)
    assert report.passed is False  # BLOCK 阻断


def test_timeline_conflict_passes_when_alive(tmp_path: Path) -> None:
    """POST_WRITE：角色在世验尸 → 不误报 timeline_conflict"""
    _seed_project(tmp_path)
    checker = ConsistencyChecker(tmp_path)
    report = checker.check(
        CheckTrigger.POST_WRITE,
        ctx={"chapter_text": "周伯蹲在尸身旁，低声道：尸体会说话。"},
    )
    assert not any(c.rule_id == "timeline_conflict" for c in report.conflicts)


def test_relation_conflict_fires_for_dead_with_active_edge(tmp_path: Path) -> None:
    """POST_WRITE：断言周伯已故，且关系网有互动型活跃边 → WARN"""
    _seed_project(tmp_path)
    checker = ConsistencyChecker(tmp_path)
    report = checker.check(
        CheckTrigger.POST_WRITE,
        ctx={"chapter_text": "李承安想起，周伯早已故去。"},
    )
    assert any(c.rule_id == "relation_conflict" for c in report.conflicts)


def test_golden_finger_overstep_fires(tmp_path: Path) -> None:
    """POST_WRITE：角色禁用词含系统/金手指，本章却触发系统 → WARN"""
    _seed_project(tmp_path)
    checker = ConsistencyChecker(tmp_path)
    report = checker.check(
        CheckTrigger.POST_WRITE,
        ctx={"chapter_text": "李承安脑海中的系统猛然激活，金手指展开。"},
    )
    assert any(c.rule_id == "golden_finger_overstep" for c in report.conflicts)


def test_realm_overstep_fires_on_unregistered(tmp_path: Path) -> None:
    """POST_WRITE：宣称突破至未登记境界 → WARN"""
    _seed_project(tmp_path)
    checker = ConsistencyChecker(tmp_path)
    report = checker.check(
        CheckTrigger.POST_WRITE,
        ctx={"chapter_text": "李承安突破至化神境，气息暴涨。"},
    )
    assert any(c.rule_id == "realm_overstep" for c in report.conflicts)


def test_realm_overstep_no_false_on_registered(tmp_path: Path) -> None:
    """POST_WRITE：突破至已登记境界 → 不误报"""
    _seed_project(tmp_path)
    checker = ConsistencyChecker(tmp_path)
    report = checker.check(
        CheckTrigger.POST_WRITE,
        ctx={"chapter_text": "李承安突破至练气境，气息微涨。"},
    )
    assert not any(c.rule_id == "realm_overstep" for c in report.conflicts)


def test_to_markdown_no_raise(tmp_path: Path) -> None:
    """to_markdown() 不再抛 NotImplementedError"""
    _seed_project(tmp_path)
    checker = ConsistencyChecker(tmp_path)
    report = checker.check(
        CheckTrigger.POST_WRITE, ctx={"chapter_text": "周伯早已故去。"}
    )
    md = report.to_markdown()
    assert isinstance(md, str) and "一致性" in md
