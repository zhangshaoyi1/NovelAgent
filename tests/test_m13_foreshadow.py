"""M13 伏笔管理单元测试

覆盖：
- foreshadows.md 表格解析
- 统计计算（总数/未埋/已埋/已回收/已废弃/回收率）
- 逾期检查（预期回收点已过）
- 章节任务检查（本章应埋/应回收）
- 支线结束检查
- 完结报告生成（foreshadow_report.md）
- 状态更新（未埋→已埋→已回收）
- CLI 命令注册
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.core.state_machine import State, StateMachine
from agent.workflows.m13_foreshadow import (
    Foreshadow,
    ForeshadowStats,
    M13ForeshadowWorkflow,
    M13Report,
)


# ============================================================
# 夹具
# ============================================================
FORESHADOW_MD = """# 伏笔登记表

> 状态：未埋 / 已埋 / 已回收 / 已废弃

| ID | 伏笔内容 | 埋设位置 | 预期回收点 | 状态 | 关联角色 |
|---|---|---|---|---|---|
| F-01 | 太虚镜情感乱码 | S01/E01/ch003 | S04/E01/ch095 | 未埋 | 太虚镜, 林寻 |
| F-02 | 锻体术暗门 | S01/E01/ch005 | S03/E02/ch068 | 已埋 | 林寻, 赵无极 |
| F-03 | 陈默金属碎片 | S01/E01/ch002 | S03/E01/ch055 | 已回收 | 陈默 |
| F-04 | 苏婉儿眼神闪躲 | S01/E03/ch025 | S02/E02/ch040 | 已埋 | 苏婉儿 |
| F-05 | 陆沉重复起手式 | S02/E01/ch050 | S03/E02/ch072 | 未埋 | 陆沉 |
| F-06 | 载体不兼容提示 | S03/E02/ch078 | S04/E02/ch115 | 已废弃 | 太虚镜 |

## 统计

- 未埋：2
- 已埋：2
- 已回收：1
- 已废弃：1
- 回收率：16.7%
"""


def _build_project(tmp_path: Path, total_written: int = 50) -> Path:
    d = tmp_path / "p"
    d.mkdir(parents=True)
    (d / "foreshadows.md").write_text(FORESHADOW_MD, encoding="utf-8")
    sm = StateMachine(d)
    sm.load()
    sm.state = State.WRITING
    sm.progress = {"total_written": total_written, "current_chapter": total_written}
    sm.save()
    return d


# ============================================================
# Test 表格解析
# ============================================================
class TestParseTable:
    def test_parse_six_foreshadows(self, tmp_path: Path) -> None:
        d = _build_project(tmp_path)
        wf = M13ForeshadowWorkflow(project_dir=d)
        items = wf.load_foreshadows()
        assert len(items) == 6

    def test_parse_fields_correct(self, tmp_path: Path) -> None:
        d = _build_project(tmp_path)
        wf = M13ForeshadowWorkflow(project_dir=d)
        items = wf.load_foreshadows()
        f01 = next(f for f in items if f.fid == "F-01")
        assert f01.content == "太虚镜情感乱码"
        assert f01.planted_at == "S01/E01/ch003"
        assert f01.expected_resolve == "S04/E01/ch095"
        assert f01.state == "未埋"
        assert "太虚镜" in f01.related_characters

    def test_parse_empty_file(self, tmp_path: Path) -> None:
        d = tmp_path / "p"
        d.mkdir()
        (d / "foreshadows.md").write_text("# 伏笔表\n\n无内容", encoding="utf-8")
        wf = M13ForeshadowWorkflow(project_dir=d)
        assert wf.load_foreshadows() == []

    def test_parse_no_file(self, tmp_path: Path) -> None:
        d = tmp_path / "p"
        d.mkdir()
        wf = M13ForeshadowWorkflow(project_dir=d)
        assert wf.load_foreshadows() == []


# ============================================================
# Test 统计
# ============================================================
class TestStats:
    def test_stats_counts(self, tmp_path: Path) -> None:
        d = _build_project(tmp_path)
        wf = M13ForeshadowWorkflow(project_dir=d)
        stats = wf.compute_stats()
        assert stats.total == 6
        assert stats.not_planted == 2  # F-01, F-05
        assert stats.planted == 2      # F-02, F-04
        assert stats.resolved == 1     # F-03
        assert stats.abandoned == 1    # F-06

    def test_resolve_rate(self, tmp_path: Path) -> None:
        d = _build_project(tmp_path)
        wf = M13ForeshadowWorkflow(project_dir=d)
        stats = wf.compute_stats()
        # 1/6 ≈ 0.167
        assert abs(stats.resolve_rate - 1 / 6) < 0.01

    def test_resolve_rate_empty(self, tmp_path: Path) -> None:
        d = tmp_path / "p"
        d.mkdir()
        wf = M13ForeshadowWorkflow(project_dir=d)
        stats = wf.compute_stats()
        assert stats.resolve_rate == 0.0


# ============================================================
# Test 逾期检查
# ============================================================
class TestOverdue:
    def test_overdue_detection(self, tmp_path: Path) -> None:
        """当前章节 50，F-04 预期回收 ch040 → 逾期"""
        d = _build_project(tmp_path, total_written=50)
        wf = M13ForeshadowWorkflow(project_dir=d)
        items = wf.load_foreshadows()
        stats = wf.compute_stats(items, current_chapter=50)
        # F-04（已埋，ch040）和 F-01（未埋，ch095）→ 只有 F-04 逾期
        assert stats.overdue == 1

    def test_not_overdue_when_before_expected(self, tmp_path: Path) -> None:
        """当前章节 10，无逾期"""
        d = _build_project(tmp_path, total_written=10)
        wf = M13ForeshadowWorkflow(project_dir=d)
        stats = wf.compute_stats(current_chapter=10)
        assert stats.overdue == 0

    def test_overdue_excludes_resolved(self, tmp_path: Path) -> None:
        """已回收的伏笔不算逾期"""
        d = _build_project(tmp_path, total_written=200)
        wf = M13ForeshadowWorkflow(project_dir=d)
        items = wf.load_foreshadows()
        f03 = next(f for f in items if f.fid == "F-03")  # 已回收
        assert f03._overdue_impl(200) is False

    def test_overdue_excludes_abandoned(self, tmp_path: Path) -> None:
        d = _build_project(tmp_path, total_written=200)
        wf = M13ForeshadowWorkflow(project_dir=d)
        items = wf.load_foreshadows()
        f06 = next(f for f in items if f.fid == "F-06")  # 已废弃
        assert f06._overdue_impl(200) is False


# ============================================================
# Test 章节任务检查
# ============================================================
class TestChapterCheck:
    def test_check_plant_task_ch003(self, tmp_path: Path) -> None:
        """ch003 应埋 F-01"""
        d = _build_project(tmp_path)
        wf = M13ForeshadowWorkflow(project_dir=d)
        tasks = wf.check_chapter_tasks(3)
        assert len(tasks["plant"]) >= 1
        assert any("F-01" in t for t in tasks["plant"])

    def test_check_no_plant_for_unplanned_chapter(self, tmp_path: Path) -> None:
        """ch010 无埋设任务"""
        d = _build_project(tmp_path)
        wf = M13ForeshadowWorkflow(project_dir=d)
        tasks = wf.check_chapter_tasks(10)
        assert tasks["plant"] == []

    def test_check_resolve_at_ch040(self, tmp_path: Path) -> None:
        """ch040 应回收 F-04（已埋）"""
        d = _build_project(tmp_path)
        wf = M13ForeshadowWorkflow(project_dir=d)
        tasks = wf.check_chapter_tasks(40)
        assert any("F-04" in t for t in tasks["resolve"])

    def test_check_force_resolve_every_10_chapters(self, tmp_path: Path) -> None:
        """每 10 章强制回收"""
        d = _build_project(tmp_path)
        wf = M13ForeshadowWorkflow(project_dir=d)
        tasks = wf.check_chapter_tasks(10)
        # 有已埋伏笔时应有强制回收任务
        assert len(tasks["resolve"]) >= 1


# ============================================================
# Test 支线结束检查
# ============================================================
class TestSublineCheck:
    def test_check_s01_unresolved(self, tmp_path: Path) -> None:
        """S01 支线有未回收伏笔"""
        d = _build_project(tmp_path)
        wf = M13ForeshadowWorkflow(project_dir=d)
        unresolved = wf.check_subline_end("S01_器灵人性觉醒")
        # F-01(未埋) F-02(已埋) F-03(已回收不包含) F-04(已埋)
        ids = [f.fid for f in unresolved]
        assert "F-01" in ids
        assert "F-02" in ids
        assert "F-04" in ids
        assert "F-03" not in ids  # 已回收

    def test_check_s02_unresolved(self, tmp_path: Path) -> None:
        d = _build_project(tmp_path)
        wf = M13ForeshadowWorkflow(project_dir=d)
        unresolved = wf.check_subline_end("S02_寒门众生觉醒")
        ids = [f.fid for f in unresolved]
        # F-05（S02 埋设，未埋）应包含
        assert "F-05" in ids

    def test_check_invalid_subline(self, tmp_path: Path) -> None:
        d = _build_project(tmp_path)
        wf = M13ForeshadowWorkflow(project_dir=d)
        unresolved = wf.check_subline_end("invalid")
        assert unresolved == []


# ============================================================
# Test 完结报告
# ============================================================
class TestCompletionReport:
    def test_generate_report_creates_file(self, tmp_path: Path) -> None:
        d = _build_project(tmp_path, total_written=50)
        wf = M13ForeshadowWorkflow(project_dir=d)
        report = wf.generate_completion_report()
        assert report.report_file is not None
        assert report.report_file.exists()
        assert report.report_file.name == "foreshadow_report.md"

    def test_report_stats_correct(self, tmp_path: Path) -> None:
        d = _build_project(tmp_path, total_written=50)
        wf = M13ForeshadowWorkflow(project_dir=d)
        report = wf.generate_completion_report()
        assert report.stats.total == 6
        assert report.stats.resolved == 1
        assert report.stats.overdue >= 1  # F-04 逾期

    def test_report_unresolved_list(self, tmp_path: Path) -> None:
        d = _build_project(tmp_path, total_written=50)
        wf = M13ForeshadowWorkflow(project_dir=d)
        report = wf.generate_completion_report()
        # 未回收 = 未埋 + 已埋 = 4
        assert len(report.unresolved) == 4

    def test_report_overdue_list(self, tmp_path: Path) -> None:
        d = _build_project(tmp_path, total_written=50)
        wf = M13ForeshadowWorkflow(project_dir=d)
        report = wf.generate_completion_report()
        # F-04 逾期
        assert len(report.overdue) >= 1

    def test_report_content_has_sections(self, tmp_path: Path) -> None:
        d = _build_project(tmp_path, total_written=50)
        wf = M13ForeshadowWorkflow(project_dir=d)
        report = wf.generate_completion_report()
        content = report.report_file.read_text(encoding="utf-8")
        assert "# 伏笔回收报告" in content
        assert "## 统计总览" in content
        assert "## 未回收伏笔清单" in content
        assert "## 处理建议" in content
        assert "回收率" in content


# ============================================================
# Test 状态更新
# ============================================================
class TestUpdateState:
    def test_update_state_unplanted_to_planted(self, tmp_path: Path) -> None:
        d = _build_project(tmp_path)
        wf = M13ForeshadowWorkflow(project_dir=d)
        result = wf.update_state("F-01", "已埋")
        assert result is True
        items = wf.load_foreshadows()
        f01 = next(f for f in items if f.fid == "F-01")
        assert f01.state == "已埋"

    def test_update_state_to_resolved(self, tmp_path: Path) -> None:
        d = _build_project(tmp_path)
        wf = M13ForeshadowWorkflow(project_dir=d)
        wf.update_state("F-02", "已回收")
        items = wf.load_foreshadows()
        f02 = next(f for f in items if f.fid == "F-02")
        assert f02.state == "已回收"

    def test_update_state_invalid_raises(self, tmp_path: Path) -> None:
        d = _build_project(tmp_path)
        wf = M13ForeshadowWorkflow(project_dir=d)
        with pytest.raises(ValueError, match="非法状态"):
            wf.update_state("F-01", "invalid")

    def test_update_state_nonexistent_fid(self, tmp_path: Path) -> None:
        d = _build_project(tmp_path)
        wf = M13ForeshadowWorkflow(project_dir=d)
        result = wf.update_state("F-99", "已埋")
        assert result is False

    def test_update_recomputes_stats(self, tmp_path: Path) -> None:
        d = _build_project(tmp_path)
        wf = M13ForeshadowWorkflow(project_dir=d)
        wf.update_state("F-01", "已埋")
        # 重新统计
        stats = wf.compute_stats()
        assert stats.not_planted == 1  # 原来是 2，F-01 变已埋后是 1
        assert stats.planted == 3      # 原来是 2，+1


# ============================================================
# Test CLI 命令注册
# ============================================================
class TestCLICommands:
    def test_foreshadow_report_registered(self) -> None:
        from agent import cli as cli_module

        assert callable(getattr(cli_module, "foreshadow_report", None))

    def test_foreshadow_check_registered(self) -> None:
        from agent import cli as cli_module

        assert callable(getattr(cli_module, "foreshadow_check", None))
