"""E3 前置式动态冲突检测与仲裁（M5 生成前门禁）单元测试

覆盖：
- 高严重度冲突 → 抛出 PreValidationBlocked，并将拦截记录写入 world.md 修订日志
- 低/中严重度冲突 → 自动仲裁，记录到 world.md 修订日志后继续生成
- 无冲突 → 正常继续生成，不产生仲裁记录
- PreValidationBlocked.report 即 ConflictReport
- pre_validate=False 时跳过门禁（向后兼容 / 关闭增强）
"""

from __future__ import annotations

import frontmatter
from pathlib import Path

from agent.core.infra.conflict_service Conflict, ConflictReport
from agent.core.base.exceptions import PreValidationBlocked
from agent.core.story.setting_manager import SettingManager
from agent.core.engine.state_machine import StateMachine
from agent.workflows.m5_write_chapter import (
    M5WriteChapterWorkflow,
)

from tests.conftest import (
    _build_minimal_project,
    _build_mock_llm,
)


class FakeConflictArbiter:
    """可注入的假冲突仲裁器，记录调用并返回预设报告"""

    def __init__(self, report: ConflictReport) -> None:
        self.report = report
        self.calls: list[tuple[str, str | None]] = []

    def check_new_setting(
        self, new_setting: str, subline_id: str | None = None
    ) -> ConflictReport:
        self.calls.append((new_setting, subline_id))
        return self.report


def _revision_log(project_dir: Path) -> list[str]:
    return list(SettingManager(project_dir).load_world()["metadata"].get("revision_log", []) or [])


# ============================================================
# 高严重度：中断生成
# ============================================================
class TestHighSeverityBlocks:
    def test_high_severity_raises_blocked(self, tmp_path: Path) -> None:
        d = _build_minimal_project(tmp_path)
        report = ConflictReport(
            conflicts=[
                Conflict(
                    field="境界体系",
                    existing="炼气",
                    new="化神",
                    severity="high",
                    suggestion="需用户确认",
                )
            ],
            summary="高严重度冲突：境界体系被越级修改",
        )
        arbiter = FakeConflictArbiter(report)
        wf = M5WriteChapterWorkflow(
            project_dir=d,
            llm_client=_build_mock_llm(),
            conflict_arbiter=arbiter,
            pre_validate=True,
        )
        wf.state_machine.load()
        ctx = wf._load_context()
        try:
            wf._pre_validation(ctx)
            raise AssertionError("期望 PreValidationBlocked")
        except PreValidationBlocked as e:
            assert e.report is report

    def test_high_severity_writes_revision_log(self, tmp_path: Path) -> None:
        d = _build_minimal_project(tmp_path)
        report = ConflictReport(
            conflicts=[
                Conflict(
                    field="境界体系",
                    existing="炼气",
                    new="化神",
                    severity="high",
                    suggestion="",
                )
            ],
            summary="高严重度冲突",
        )
        arbiter = FakeConflictArbiter(report)
        wf = M5WriteChapterWorkflow(
            project_dir=d,
            llm_client=_build_mock_llm(),
            conflict_arbiter=arbiter,
            pre_validate=True,
        )
        # 章节文件不应被创建（生成前已中断）
        wf.state_machine.load()
        ctx = wf._load_context()
        import pytest

        with pytest.raises(PreValidationBlocked):
            wf._pre_validation(ctx)

        assert (d / "chapters" / "ch001.md").exists() is False
        log = _revision_log(d)
        assert any("[仲裁-高]" in entry for entry in log)
        assert "境界体系" in log[-1]

    def test_arbiter_receives_planned_setting(self, tmp_path: Path) -> None:
        d = _build_minimal_project(tmp_path)
        report = ConflictReport(
            conflicts=[
                Conflict(field="x", existing="a", new="b", severity="high", suggestion="")
            ],
            summary="",
        )
        arbiter = FakeConflictArbiter(report)
        wf = M5WriteChapterWorkflow(
            project_dir=d,
            llm_client=_build_mock_llm(),
            conflict_arbiter=arbiter,
            pre_validate=True,
        )
        wf.state_machine.load()
        ctx = wf._load_context()
        try:
            wf._pre_validation(ctx)
        except PreValidationBlocked:
            pass
        # 门禁应传入 "计划设定变更" 文本与 subline_id
        assert arbiter.calls
        planned, subline_id = arbiter.calls[0]
        assert "计划设定变更" in planned
        assert subline_id == "S01_器灵人性觉醒"


# ============================================================
# 低/中严重度：自动仲裁后继续
# ============================================================
class TestLowSeverityAutoResolve:
    def test_low_severity_continues_and_logs(self, tmp_path: Path) -> None:
        d = _build_minimal_project(tmp_path)
        report = ConflictReport(
            conflicts=[
                Conflict(
                    field="支线目标",
                    existing="a",
                    new="b",
                    severity="low",
                    suggestion="自动采用新设定，继续生成",
                )
            ],
            summary="低严重度冲突",
        )
        arbiter = FakeConflictArbiter(report)
        wf = M5WriteChapterWorkflow(
            project_dir=d,
            llm_client=_build_mock_llm(),
            conflict_arbiter=arbiter,
            pre_validate=True,
        )
        wf.state_machine.load()
        ctx = wf._load_context()
        result = wf._pre_validation(ctx)  # 不应抛异常
        assert result["decision"] == "continue"
        log = _revision_log(d)
        assert any("[仲裁-自动]" in entry for entry in log)
        assert "支线目标" in log[-1]


# ============================================================
# 无冲突：正常继续
# ============================================================
class TestNoConflictContinues:
    def test_no_conflict_generates_chapter(self, tmp_path: Path) -> None:
        d = _build_minimal_project(tmp_path)
        report = ConflictReport(conflicts=[], summary="无冲突")
        arbiter = FakeConflictArbiter(report)
        wf = M5WriteChapterWorkflow(
            project_dir=d,
            llm_client=_build_mock_llm(),
            conflict_arbiter=arbiter,
            pre_validate=True,
        )
        wf.state_machine.load()
        ctx = wf._load_context()
        result = wf._pre_validation(ctx)
        assert result["decision"] == "continue"
        # 无仲裁记录写入修订日志
        log = _revision_log(d)
        assert all("[仲裁" not in entry for entry in log)


# ============================================================
# 开关：pre_validate=False 跳过门禁
# ============================================================
class TestPreValidateToggle:
    def test_pre_validate_false_skips_gate(self, tmp_path: Path) -> None:
        d = _build_minimal_project(tmp_path)
        # 即便返回高严重度报告，pre_validate=False 也应跳过
        report = ConflictReport(
            conflicts=[
                Conflict(field="x", existing="a", new="b", severity="high", suggestion="")
            ],
            summary="",
        )
        arbiter = FakeConflictArbiter(report)
        wf = M5WriteChapterWorkflow(
            project_dir=d,
            llm_client=_build_mock_llm(),
            conflict_arbiter=arbiter,
            pre_validate=False,
        )
        wf.state_machine.load()
        ctx = wf._load_context()
        result = wf._pre_validation(ctx)  # 不应抛 PreValidationBlocked
        assert result["decision"] == "continue"
        assert arbiter.calls == []  # 门禁未触发
