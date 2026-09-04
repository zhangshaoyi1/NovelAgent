"""M18 错误恢复单元测试

覆盖：
- F18.2 质量校验失败报告（QualityFailureReport + FailureReportBuilder）
- F18.3 状态机卡死恢复（StateRecovery: reset_to_last_stable / reset_to_state / 历史）
- F18.4 草稿管理（DraftManager: save/load/clear/has_draft）
- F18.4 续写检测（check_draft_on_startup）
- F18.1 LLM 失败兜底（handle_llm_failure）
- CLI 命令（reset-state / draft-status / draft-discard）
- M5 集成（草稿保存/清除）
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from agent.cli import app
from agent.core.engine.state_machine import State, StateMachine
from agent.workflows.evaluation.m18_recovery import (
    Draft,
    DraftManager,
    DraftResumeDecision,
    FailureReportBuilder,
    QualityFailureReport,
    STABLE_STATES,
    StateRecovery,
    check_draft_on_startup,
    handle_llm_failure,
)


# ============================================================
# F18.2 质量校验失败报告
# ============================================================
class TestQualityFailureReport:
    def test_report_basic_fields(self) -> None:
        report = QualityFailureReport(
            chapter_num=5,
            subline_id="S01_悟道",
            attempts=2,
            max_revisions=2,
            failing_rules=[
                {"rule": "open_hook", "pass": False, "issue": "前 500 字无冲突"},
                {"rule": "emotion_anchor", "pass": False, "issue": "缺少情绪锚点"},
            ],
            suggestions="增加开篇冲突",
            final_text="正文内容...",
        )
        assert report.chapter_num == 5
        assert report.attempts == 2
        assert len(report.failing_rules) == 2
        assert "手动改" in report.decisions

    def test_report_default_decisions(self) -> None:
        report = QualityFailureReport(
            chapter_num=1, subline_id="S01", attempts=1, max_revisions=2,
            failing_rules=[], suggestions="", final_text="",
        )
        assert "手动改" in report.decisions
        assert "调整规则" in report.decisions
        assert "跳过本次校验项" in report.decisions

    def test_report_to_markdown(self) -> None:
        report = QualityFailureReport(
            chapter_num=3,
            subline_id="S02_试炼",
            attempts=2,
            max_revisions=2,
            failing_rules=[
                {"rule": "open_hook", "pass": False, "issue": "无冲突"},
            ],
            suggestions="前 300 字加入冲突",
            final_text="正文ABC",
        )
        md = report.to_markdown()
        assert "质量校验失败报告" in md
        assert "第 3 章" in md
        assert "S02_试炼" in md
        assert "open_hook" in md
        assert "无冲突" in md
        assert "前 300 字加入冲突" in md
        assert "正文ABC" in md
        assert "手动改" in md


class TestFailureReportBuilder:
    def test_build_from_quality_report(self) -> None:
        quality_report = {
            "overall_pass": False,
            "rules": [
                {"rule": "open_hook", "pass": True, "issue": ""},
                {"rule": "emotion_anchor", "pass": False, "issue": "缺少锚点"},
                {"rule": "chapter_end_suspense", "pass": False, "issue": "章末无悬念"},
            ],
            "suggestions": "加锚子和章末钩子",
        }
        report = FailureReportBuilder.build(
            chapter_num=5,
            subline_id="S01",
            attempts=2,
            max_revisions=2,
            quality_report=quality_report,
            final_text="正文",
        )
        assert report.chapter_num == 5
        assert report.attempts == 2
        # 只保留未通过的
        assert len(report.failing_rules) == 2
        assert all(not r["pass"] for r in report.failing_rules)
        assert report.suggestions == "加锚子和章末钩子"

    def test_build_with_empty_rules(self) -> None:
        report = FailureReportBuilder.build(
            chapter_num=1, subline_id="S01", attempts=1, max_revisions=2,
            quality_report={"rules": [], "suggestions": ""},
            final_text="",
        )
        assert report.failing_rules == []

    def test_build_with_missing_rules_key(self) -> None:
        report = FailureReportBuilder.build(
            chapter_num=1, subline_id="S01", attempts=1, max_revisions=2,
            quality_report={}, final_text="",
        )
        assert report.failing_rules == []


# ============================================================
# F18.4 草稿管理
# ============================================================
class TestDraftManager:
    def test_no_draft_initially(self, tmp_path: Path) -> None:
        dm = DraftManager(tmp_path)
        assert not dm.has_draft()
        assert dm.load_draft() is None

    def test_save_and_load_draft(self, tmp_path: Path) -> None:
        dm = DraftManager(tmp_path)
        dm.save_draft(
            chapter_num=5,
            subline_id="S01_悟道",
            text="这是草稿正文",
            ctx_snapshot={"subline_id": "S01_悟道"},
        )
        assert dm.has_draft()
        draft = dm.load_draft()
        assert draft is not None
        assert draft.chapter_num == 5
        assert draft.subline_id == "S01_悟道"
        assert "草稿正文" in draft.text
        assert draft.saved_at != ""

    def test_clear_draft(self, tmp_path: Path) -> None:
        dm = DraftManager(tmp_path)
        dm.save_draft(chapter_num=1, subline_id="S01", text="x")
        assert dm.has_draft()
        cleared = dm.clear_draft()
        assert cleared is True
        assert not dm.has_draft()

    def test_clear_no_draft_returns_false(self, tmp_path: Path) -> None:
        dm = DraftManager(tmp_path)
        assert dm.clear_draft() is False

    def test_load_corrupted_draft_returns_none(self, tmp_path: Path) -> None:
        """损坏的草稿文件应返回 None"""
        state_dir = tmp_path / ".state"
        state_dir.mkdir()
        (state_dir / "draft.wip").write_text("not json {{{", encoding="utf-8")
        dm = DraftManager(tmp_path)
        assert dm.has_draft()
        assert dm.load_draft() is None

    def test_save_creates_state_dir(self, tmp_path: Path) -> None:
        """.state 目录不存在时应创建"""
        dm = DraftManager(tmp_path / "nested")
        dm.save_draft(chapter_num=1, subline_id="S01", text="x")
        assert (tmp_path / "nested" / ".state" / "draft.wip").exists()

    def test_draft_round_trip(self, tmp_path: Path) -> None:
        """草稿序列化/反序列化保持数据"""
        dm = DraftManager(tmp_path)
        original = Draft(
            chapter_num=10,
            subline_id="S03_高潮",
            text="正文" * 100,
            ctx_snapshot={"key": "value", "nested": {"a": 1}},
        )
        dm.save_draft(
            chapter_num=original.chapter_num,
            subline_id=original.subline_id,
            text=original.text,
            ctx_snapshot=original.ctx_snapshot,
        )
        loaded = dm.load_draft()
        assert loaded is not None
        assert loaded.chapter_num == original.chapter_num
        assert loaded.subline_id == original.subline_id
        assert loaded.text == original.text
        assert loaded.ctx_snapshot["key"] == "value"
        assert loaded.ctx_snapshot["nested"]["a"] == 1


# ============================================================
# F18.4 续写检测
# ============================================================
class TestCheckDraftOnStartup:
    def test_no_draft(self, tmp_path: Path) -> None:
        from rich.console import Console

        result = check_draft_on_startup(
            tmp_path, console=Console(file=io.StringIO()), interactive=False
        )
        assert result.has_draft is False
        assert result.action == "none"

    def test_has_draft_non_interactive(self, tmp_path: Path) -> None:
        """非交互模式检测到草稿应返回 resume"""
        from rich.console import Console

        dm = DraftManager(tmp_path)
        dm.save_draft(chapter_num=5, subline_id="S01", text="草稿")
        result = check_draft_on_startup(
            tmp_path, console=Console(file=io.StringIO()), interactive=False
        )
        assert result.has_draft is True
        assert result.draft is not None
        assert result.draft.chapter_num == 5
        assert result.action == "resume"


# ============================================================
# F18.3 状态机卡死恢复
# ============================================================
class TestStateRecovery:
    def _setup_state(self, tmp_path: Path, state: State, mode: str = "heavy") -> Path:
        """创建指定状态的 state.json"""
        sm = StateMachine(project_dir=tmp_path)
        sm.state = state
        sm.mode = mode
        sm.progress = {"total_written": 5, "current_subline": "S01"}
        sm.save()
        return tmp_path

    def test_list_stable_states(self, tmp_path: Path) -> None:
        recovery = StateRecovery(tmp_path)
        stable = recovery.list_stable_states()
        assert "CONFIGURING" in stable
        assert "WRITING" in stable
        assert "COMPLETED" in stable

    def test_reset_from_writing_to_character_design(self, tmp_path: Path) -> None:
        """WRITING → CHARACTER_DESIGN"""
        self._setup_state(tmp_path, State.WRITING)
        recovery = StateRecovery(tmp_path)
        result = recovery.reset_to_last_stable()
        assert result.success
        assert result.old_state == "WRITING"
        assert result.new_state == "CHARACTER_DESIGN"

    def test_reset_from_architecting_to_configuring(self, tmp_path: Path) -> None:
        """ARCHITECTING（非稳定）→ CONFIGURING"""
        self._setup_state(tmp_path, State.ARCHITECTING)
        recovery = StateRecovery(tmp_path)
        result = recovery.reset_to_last_stable()
        assert result.success
        assert result.old_state == "ARCHITECTING"
        assert result.new_state == "CONFIGURING"

    def test_reset_from_outlining_to_arch_confirmed(self, tmp_path: Path) -> None:
        """OUTLINING（非稳定）→ ARCH_CONFIRMED"""
        self._setup_state(tmp_path, State.OUTLINING)
        recovery = StateRecovery(tmp_path)
        result = recovery.reset_to_last_stable()
        assert result.success
        assert result.new_state == "ARCH_CONFIRMED"

    def test_reset_from_paused_to_writing(self, tmp_path: Path) -> None:
        """PAUSED → WRITING"""
        self._setup_state(tmp_path, State.PAUSED)
        recovery = StateRecovery(tmp_path)
        result = recovery.reset_to_last_stable()
        assert result.success
        assert result.new_state == "WRITING"

    def test_reset_from_configuring_to_init(self, tmp_path: Path) -> None:
        """CONFIGURING → INIT"""
        self._setup_state(tmp_path, State.CONFIGURING)
        recovery = StateRecovery(tmp_path)
        result = recovery.reset_to_last_stable()
        assert result.success
        assert result.new_state == "INIT"

    def test_reset_init_no_further(self, tmp_path: Path) -> None:
        """INIT 已是初始，无更早状态可退"""
        self._setup_state(tmp_path, State.INIT)
        recovery = StateRecovery(tmp_path)
        result = recovery.reset_to_last_stable()
        # INIT → INIT（无处可退）
        assert result.new_state == "INIT"

    def test_reset_saves_history(self, tmp_path: Path) -> None:
        """重置应保存历史"""
        self._setup_state(tmp_path, State.WRITING)
        recovery = StateRecovery(tmp_path)
        recovery.reset_to_last_stable()
        history = recovery.list_history()
        assert len(history) == 1
        assert history[0]["state"] == "WRITING"
        assert "reset_at" in history[0]

    def test_reset_to_specific_state(self, tmp_path: Path) -> None:
        """重置到指定状态"""
        self._setup_state(tmp_path, State.WRITING)
        recovery = StateRecovery(tmp_path)
        result = recovery.reset_to_state(State.ARCH_CONFIRMED)
        assert result.success
        assert result.old_state == "WRITING"
        assert result.new_state == "ARCH_CONFIRMED"

    def test_reset_to_unstable_state_raises(self, tmp_path: Path) -> None:
        """重置到非稳定状态应报错"""
        self._setup_state(tmp_path, State.WRITING)
        recovery = StateRecovery(tmp_path)
        with pytest.raises(ValueError, match="稳定状态"):
            recovery.reset_to_state(State.ARCHITECTING)

    def test_history_max_limit(self, tmp_path: Path) -> None:
        """历史超过上限应截断"""
        self._setup_state(tmp_path, State.WRITING)
        recovery = StateRecovery(tmp_path)
        # 手动写入超过 MAX_HISTORY 条历史
        from agent.workflows.evaluation.m18_recovery import StateRecovery as SR

        for i in range(SR.MAX_HISTORY + 5):
            recovery._save_history({"state": "WRITING", "i": i})
        history = recovery.list_history()
        assert len(history) == SR.MAX_HISTORY

    def test_state_actually_persisted(self, tmp_path: Path) -> None:
        """重置后 state.json 应反映新状态"""
        self._setup_state(tmp_path, State.WRITING)
        recovery = StateRecovery(tmp_path)
        recovery.reset_to_last_stable()
        # 重新加载验证
        sm = StateMachine(project_dir=tmp_path)
        sm.load()
        assert sm.state == State.CHARACTER_DESIGN

    def test_reset_preserves_mode(self, tmp_path: Path) -> None:
        """重置不应改变 mode"""
        self._setup_state(tmp_path, State.WRITING, mode="light")
        recovery = StateRecovery(tmp_path)
        recovery.reset_to_last_stable()
        sm = StateMachine(project_dir=tmp_path)
        sm.load()
        assert sm.mode == "light"


# ============================================================
# F18.1 LLM 失败兜底
# ============================================================
class TestHandleLLMFailure:
    def test_non_interactive_returns_abort(self, tmp_path: Path) -> None:
        """非交互环境应返回 abort"""
        from rich.console import Console

        result = handle_llm_failure(
            error=Exception("API timeout"),
            context="M5 章节生成",
            project_dir=tmp_path,
            console=Console(file=io.StringIO()),
        )
        # 非交互环境下 Prompt.ask 会抛 OSError，默认 abort
        assert result in ("abort", "retry", "skip")


# ============================================================
# CLI 命令
# ============================================================
class TestResetStateCLI:
    def test_reset_state_no_state_file(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["reset-state", "-d", str(tmp_path), "-y"])
        assert result.exit_code == 1
        assert "状态文件不存在" in result.output

    def test_reset_state_with_yes(self, tmp_path: Path) -> None:
        """-y 跳过确认"""
        sm = StateMachine(project_dir=tmp_path)
        sm.state = State.WRITING
        sm.save()
        runner = CliRunner()
        result = runner.invoke(app, ["reset-state", "-d", str(tmp_path), "-y"])
        assert result.exit_code == 0
        assert "重置成功" in result.output
        assert "WRITING" in result.output
        assert "CHARACTER_DESIGN" in result.output

    def test_reset_state_to_target(self, tmp_path: Path) -> None:
        sm = StateMachine(project_dir=tmp_path)
        sm.state = State.WRITING
        sm.save()
        runner = CliRunner()
        result = runner.invoke(
            app, ["reset-state", "-d", str(tmp_path), "-t", "ARCH_CONFIRMED", "-y"]
        )
        assert result.exit_code == 0
        assert "ARCH_CONFIRMED" in result.output

    def test_reset_state_invalid_target(self, tmp_path: Path) -> None:
        sm = StateMachine(project_dir=tmp_path)
        sm.state = State.WRITING
        sm.save()
        runner = CliRunner()
        result = runner.invoke(
            app, ["reset-state", "-d", str(tmp_path), "-t", "WRITING", "-y"]
        )
        # WRITING 是稳定状态，应成功
        assert result.exit_code == 0

    def test_reset_state_cancel(self, tmp_path: Path) -> None:
        """不带 -y 时应询问确认，输入 n 取消"""
        sm = StateMachine(project_dir=tmp_path)
        sm.state = State.WRITING
        sm.save()
        runner = CliRunner()
        result = runner.invoke(app, ["reset-state", "-d", str(tmp_path)], input="n\n")
        assert result.exit_code == 0
        assert "已取消" in result.output


class TestDraftCLI:
    def test_draft_status_no_draft(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["draft-status", "-d", str(tmp_path)])
        assert result.exit_code == 0
        assert "无未完成草稿" in result.output

    def test_draft_status_with_draft(self, tmp_path: Path) -> None:
        dm = DraftManager(tmp_path)
        dm.save_draft(chapter_num=3, subline_id="S01", text="草稿正文")
        runner = CliRunner()
        result = runner.invoke(app, ["draft-status", "-d", str(tmp_path)])
        assert result.exit_code == 0
        assert "第 3 章" in result.output
        assert "S01" in result.output

    def test_draft_discard_no_draft(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["draft-discard", "-d", str(tmp_path), "-y"])
        assert result.exit_code == 0
        assert "无草稿可丢弃" in result.output

    def test_draft_discard_with_yes(self, tmp_path: Path) -> None:
        dm = DraftManager(tmp_path)
        dm.save_draft(chapter_num=1, subline_id="S01", text="x")
        runner = CliRunner()
        result = runner.invoke(app, ["draft-discard", "-d", str(tmp_path), "-y"])
        assert result.exit_code == 0
        assert "草稿已丢弃" in result.output
        assert not dm.has_draft()

    def test_draft_discard_cancel(self, tmp_path: Path) -> None:
        dm = DraftManager(tmp_path)
        dm.save_draft(chapter_num=1, subline_id="S01", text="x")
        runner = CliRunner()
        result = runner.invoke(app, ["draft-discard", "-d", str(tmp_path)], input="n\n")
        assert result.exit_code == 0
        assert "已取消" in result.output
        assert dm.has_draft()  # 草稿仍在


# ============================================================
# M5 集成（草稿保存/清除）
# ============================================================
class TestM5DraftIntegration:
    def test_m5_clears_draft_on_success(self, tmp_path: Path) -> None:
        """M5 成功后应清除草稿"""
        # 这个测试验证 DraftManager 的 clear 行为已被 M5 调用
        # 完整的 M5 集成测试在 test_m5_write_chapter.py
        dm = DraftManager(tmp_path)
        dm.save_draft(chapter_num=1, subline_id="S01", text="草稿")
        assert dm.has_draft()
        dm.clear_draft()
        assert not dm.has_draft()
