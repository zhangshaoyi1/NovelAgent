"""T-5 ConsistencyChecker 数据化落地测试

覆盖：
- check() 不再抛 NotImplementedError，返回 ConsistencyReport
- assess_architecture_impact() 返回空壳报告（不再抛 NotImplementedError）
- 内置 field_conflict 规则委托 ConflictArbiter（mock 验证）
"""
from __future__ import annotations

from pathlib import Path

from agent.core.consistency_checker import ConsistencyChecker, ConsistencyReport, CheckTrigger


def test_check_no_longer_raises(tmp_path: Path) -> None:
    """check() 不再抛 NotImplementedError，无设定变更时无冲突（passed=True）"""
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
