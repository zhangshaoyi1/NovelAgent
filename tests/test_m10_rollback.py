"""M10 失败回退与续作恢复单元测试

覆盖：
- F10.1 分叉点回滚（章节归档 + 进度回退 + 状态门禁 + 边界条件）
- F10.2 续作恢复简报（进度/剧情线/伏笔/关系变化/建议）
- CLI 命令（rollback / resume）
- 归档目录管理
- 简报 Markdown 输出
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner

from agent.cli import app
from agent.core.engine.state_machine import State, StateMachine
from agent.workflows.m10_rollback import (
    M10ResumeWorkflow,
    M10RollbackWorkflow,
    ResumeBrief,
    RollbackResult,
)


# ============================================================
# 辅助：构造带章节的项目
# ============================================================
def setup_writing_project(
    tmp_path: Path,
    total_chapters: int = 5,
    state: State = State.WRITING,
    subline: str = "S01_悟道",
) -> Path:
    """创建一个有 N 章的项目"""
    # state.json
    sm = StateMachine(project_dir=tmp_path)
    sm.state = state
    sm.mode = "light"
    sm.progress = {
        "current_subline": subline,
        "current_chapter": total_chapters,
        "total_written": total_chapters,
        "last_written_at": "2026-08-14 10:00:00",
    }
    sm.save()

    # chapters
    chapters_dir = tmp_path / "chapters"
    chapters_dir.mkdir(parents=True, exist_ok=True)
    for i in range(1, total_chapters + 1):
        (chapters_dir / f"ch{i:03d}.md").write_text(
            f"---\nchapter_num: {i}\n---\n# 第{i}章\n正文{i}",
            encoding="utf-8",
        )

    # subline
    subline_dir = tmp_path / "sublines" / subline
    subline_dir.mkdir(parents=True, exist_ok=True)
    (subline_dir / "subline.md").write_text(
        f"---\nsubline_name: 悟道之旅\ngoal: 主角悟道\n---\n支线内容",
        encoding="utf-8",
    )

    # foreshadows
    (tmp_path / "foreshadows.md").write_text(
        "| ID | 内容 | 埋设点 | 预期回收 | 状态 | 相关角色 |\n"
        "|---|---|---|---|---|---|\n"
        "| F-01 | 神秘玉佩 | S01/ch003 | S04/ch0XX | 已埋 | 林寻 |\n"
        "| F-02 | 师父遗言 | S01/ch001 | S03/ch0XX | 未埋 | 林寻 |\n"
        "| F-03 | 旧敌未死 | S02/ch010 | S05/ch0XX | 逾期 | 赵无极 |\n"
        "| F-04 | 已回收伏笔 | S01/ch002 | S02/ch005 | 已回收 | 甲 |\n",
        encoding="utf-8",
    )

    # relations
    rel_dir = tmp_path / "relations"
    rel_dir.mkdir(parents=True, exist_ok=True)
    (rel_dir / "graph.md").write_text(
        "## 关系网\n\nA --> B 对立\n"
        "archived: A --> C 旧关系（已归档）\n",
        encoding="utf-8",
    )

    # protagonist_route
    (tmp_path / "protagonist_route.md").write_text(
        "# 主角路线\n\nN01 milestone: 觉醒\nN02 milestone: 突破\n",
        encoding="utf-8",
    )

    return tmp_path


# ============================================================
# F10.1 分叉点回滚
# ============================================================
class TestRollback:
    def test_rollback_archives_later_chapters(self, tmp_path: Path) -> None:
        """回滚应归档目标章节及之后的章节"""
        setup_writing_project(tmp_path, total_chapters=5)
        wf = M10RollbackWorkflow(tmp_path, console=Console(file=io.StringIO()))
        result = wf.rollback_to_chapter(3)

        assert result.success
        # ch003, ch004, ch005 应被归档
        assert len(result.archived_chapters) == 3
        assert "ch003.md" in result.archived_chapters
        assert "ch005.md" in result.archived_chapters
        # ch001, ch002 仍在
        assert (tmp_path / "chapters" / "ch001.md").exists()
        assert (tmp_path / "chapters" / "ch002.md").exists()
        # ch003+ 不在原位
        assert not (tmp_path / "chapters" / "ch003.md").exists()

    def test_rollback_updates_progress(self, tmp_path: Path) -> None:
        """回滚应更新进度指针"""
        setup_writing_project(tmp_path, total_chapters=5)
        wf = M10RollbackWorkflow(tmp_path, console=Console(file=io.StringIO()))
        result = wf.rollback_to_chapter(3)

        assert result.new_progress["total_written"] == 2
        assert result.new_progress["current_chapter"] == 2
        assert "last_rollback_at" in result.new_progress
        assert result.new_progress["last_rollback_target"] == 3

    def test_rollback_persists_state(self, tmp_path: Path) -> None:
        """回滚后 state.json 应反映新进度"""
        setup_writing_project(tmp_path, total_chapters=5)
        wf = M10RollbackWorkflow(tmp_path, console=Console(file=io.StringIO()))
        wf.rollback_to_chapter(3)

        sm = StateMachine(project_dir=tmp_path)
        sm.load()
        assert sm.progress["total_written"] == 2

    def test_rollback_archived_to_subdirectory(self, tmp_path: Path) -> None:
        """归档应放到 _archived/ 子目录（带时间戳）"""
        setup_writing_project(tmp_path, total_chapters=5)
        wf = M10RollbackWorkflow(tmp_path, console=Console(file=io.StringIO()))
        wf.rollback_to_chapter(4)

        archived = wf.list_archived()
        assert len(archived) == 1
        # 归档目录名含 rollback_to_4
        assert "rollback_to_4" in archived[0].name

    def test_rollback_to_first_chapter(self, tmp_path: Path) -> None:
        """回滚到第 1 章（归档所有章节）"""
        setup_writing_project(tmp_path, total_chapters=3)
        wf = M10RollbackWorkflow(tmp_path, console=Console(file=io.StringIO()))
        result = wf.rollback_to_chapter(1)

        assert result.success
        assert len(result.archived_chapters) == 3
        assert result.new_progress["total_written"] == 0

    def test_rollback_target_beyond_written_no_op(self, tmp_path: Path) -> None:
        """目标章节超过已写章节，不操作"""
        setup_writing_project(tmp_path, total_chapters=3)
        wf = M10RollbackWorkflow(tmp_path, console=Console(file=io.StringIO()))
        result = wf.rollback_to_chapter(10)

        assert not result.success
        assert "无需回滚" in result.message
        # 章节仍在
        assert (tmp_path / "chapters" / "ch001.md").exists()

    def test_rollback_invalid_chapter_zero(self, tmp_path: Path) -> None:
        """章节号 < 1 应报错"""
        setup_writing_project(tmp_path, total_chapters=3)
        wf = M10RollbackWorkflow(tmp_path, console=Console(file=io.StringIO()))
        with pytest.raises(ValueError, match="≥ 1"):
            wf.rollback_to_chapter(0)

    def test_rollback_wrong_state_raises(self, tmp_path: Path) -> None:
        """非 WRITING 状态应报错"""
        setup_writing_project(tmp_path, total_chapters=3, state=State.CHARACTER_DESIGN)
        wf = M10RollbackWorkflow(tmp_path, console=Console(file=io.StringIO()))
        with pytest.raises(ValueError, match="WRITING"):
            wf.rollback_to_chapter(2)

    def test_rollback_last_chapter(self, tmp_path: Path) -> None:
        """回滚最后一章（只归档一章）"""
        setup_writing_project(tmp_path, total_chapters=5)
        wf = M10RollbackWorkflow(tmp_path, console=Console(file=io.StringIO()))
        result = wf.rollback_to_chapter(5)

        assert result.success
        assert len(result.archived_chapters) == 1
        assert "ch005.md" in result.archived_chapters
        assert result.new_progress["total_written"] == 4

    def test_multiple_rollbacks_not_overwrite(self, tmp_path: Path) -> None:
        """多次回滚不应覆盖之前的归档"""
        setup_writing_project(tmp_path, total_chapters=5)
        wf = M10RollbackWorkflow(tmp_path, console=Console(file=io.StringIO()))
        wf.rollback_to_chapter(3)
        # 重新写第 3、4 章
        (tmp_path / "chapters" / "ch003.md").write_text("new ch3", encoding="utf-8")
        (tmp_path / "chapters" / "ch004.md").write_text("new ch4", encoding="utf-8")
        # 再次回滚
        wf2 = M10RollbackWorkflow(tmp_path, console=Console(file=io.StringIO()))
        # 需要 WRITING 状态
        sm = StateMachine(project_dir=tmp_path)
        sm.load()
        sm.state = State.WRITING
        sm.progress["total_written"] = 4
        sm.save()
        wf2.rollback_to_chapter(3)

        archived = wf2.list_archived()
        assert len(archived) == 2  # 两次归档

    def test_parse_chapter_num(self) -> None:
        assert M10RollbackWorkflow._parse_chapter_num("ch003.md") == 3
        assert M10RollbackWorkflow._parse_chapter_num("ch001.md") == 1
        assert M10RollbackWorkflow._parse_chapter_num("readme.md") is None

    def test_list_archived_empty(self, tmp_path: Path) -> None:
        """无归档时返回空列表"""
        setup_writing_project(tmp_path, total_chapters=3)
        wf = M10RollbackWorkflow(tmp_path, console=Console(file=io.StringIO()))
        assert wf.list_archived() == []


# ============================================================
# F10.2 续作恢复简报
# ============================================================
class TestResumeBrief:
    def test_generate_brief_basic(self, tmp_path: Path) -> None:
        """生成基本简报"""
        setup_writing_project(tmp_path, total_chapters=5)
        wf = M10ResumeWorkflow(tmp_path, console=Console(file=io.StringIO()))
        brief = wf.generate_brief()

        assert brief.last_chapter == 5
        assert brief.last_subline == "S01_悟道"
        assert brief.last_written_at == "2026-08-14 10:00:00"
        assert brief.mode == "light"

    def test_brief_collects_pending_plots(self, tmp_path: Path) -> None:
        """简报应收集悬而未决的剧情线"""
        setup_writing_project(tmp_path, total_chapters=3)
        wf = M10ResumeWorkflow(tmp_path, console=Console(file=io.StringIO()))
        brief = wf.generate_brief()

        assert len(brief.pending_plots) > 0
        # 应包含当前支线目标
        assert any("悟道" in p for p in brief.pending_plots)

    def test_brief_collects_unresolved_foreshadows(self, tmp_path: Path) -> None:
        """简报应收集未回收伏笔（按优先级排序）"""
        setup_writing_project(tmp_path, total_chapters=3)
        wf = M10ResumeWorkflow(tmp_path, console=Console(file=io.StringIO()))
        brief = wf.generate_brief()

        # F-01(已埋)、F-02(未埋)、F-03(逾期)、F-04(已回收)
        # 应排除已回收的 F-04
        ids = [f["id"] for f in brief.unresolved_foreshadows]
        assert "F-04" not in ids
        assert "F-01" in ids
        assert "F-02" in ids
        assert "F-03" in ids
        # 逾期优先
        assert brief.unresolved_foreshadows[0]["id"] == "F-03"

    def test_brief_collects_relation_changes(self, tmp_path: Path) -> None:
        """简报应收集关系网变化"""
        setup_writing_project(tmp_path, total_chapters=3)
        wf = M10ResumeWorkflow(tmp_path, console=Console(file=io.StringIO()))
        brief = wf.generate_brief()

        assert len(brief.relation_changes) > 0
        assert any("archived" in c.lower() for c in brief.relation_changes)

    def test_brief_has_suggestions(self, tmp_path: Path) -> None:
        """简报应有下一步建议"""
        setup_writing_project(tmp_path, total_chapters=3)
        wf = M10ResumeWorkflow(tmp_path, console=Console(file=io.StringIO()))
        brief = wf.generate_brief()

        assert len(brief.suggestions) > 0
        # 应包含继续写下一章的建议
        assert any("第 4 章" in s for s in brief.suggestions)

    def test_brief_overdue_foreshadow_warning(self, tmp_path: Path) -> None:
        """有逾期伏笔时应有警告建议"""
        setup_writing_project(tmp_path, total_chapters=3)
        wf = M10ResumeWorkflow(tmp_path, console=Console(file=io.StringIO()))
        brief = wf.generate_brief()

        # F-03 是逾期
        assert any("逾期" in s for s in brief.suggestions)

    def test_brief_to_markdown(self, tmp_path: Path) -> None:
        """简报 Markdown 输出"""
        setup_writing_project(tmp_path, total_chapters=3)
        wf = M10ResumeWorkflow(tmp_path, console=Console(file=io.StringIO()))
        brief = wf.generate_brief()
        md = brief.to_markdown()

        assert "# 续作简报" in md
        assert "第 3 章" in md
        assert "S01_悟道" in md
        assert "悬而未决" in md
        assert "未回收伏笔" in md
        assert "F-03" in md
        assert "建议下一步" in md

    def test_brief_no_foreshadow_file(self, tmp_path: Path) -> None:
        """无 foreshadows.md 时不报错"""
        setup_writing_project(tmp_path, total_chapters=3)
        (tmp_path / "foreshadows.md").unlink()
        wf = M10ResumeWorkflow(tmp_path, console=Console(file=io.StringIO()))
        brief = wf.generate_brief()
        assert brief.unresolved_foreshadows == []

    def test_brief_no_relations_file(self, tmp_path: Path) -> None:
        """无 graph.md 时不报错"""
        setup_writing_project(tmp_path, total_chapters=3)
        (tmp_path / "relations" / "graph.md").unlink()
        wf = M10ResumeWorkflow(tmp_path, console=Console(file=io.StringIO()))
        brief = wf.generate_brief()
        assert brief.relation_changes == []

    def test_brief_pending_plots_limited_to_3(self, tmp_path: Path) -> None:
        """悬而未决剧情线最多 3 条"""
        setup_writing_project(tmp_path, total_chapters=3)
        # 添加更多支线
        for i in range(5):
            d = tmp_path / "sublines" / f"S0{i+2}_支线"
            d.mkdir(parents=True, exist_ok=True)
            (d / "subline.md").write_text("---\ngoal: x\n---\n", encoding="utf-8")
        wf = M10ResumeWorkflow(tmp_path, console=Console(file=io.StringIO()))
        brief = wf.generate_brief()
        assert len(brief.pending_plots) <= 3

    def test_show_brief_does_not_raise(self, tmp_path: Path) -> None:
        setup_writing_project(tmp_path, total_chapters=3)
        wf = M10ResumeWorkflow(
            tmp_path, console=Console(width=100, file=io.StringIO())
        )
        brief = wf.generate_brief()
        wf.show_brief(brief)


# ============================================================
# CLI 命令
# ============================================================
class TestRollbackCLI:
    def test_rollback_no_state_file(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["rollback", "-d", str(tmp_path), "-c", "1", "-y"])
        assert result.exit_code == 1
        assert "状态文件不存在" in result.output

    def test_rollback_with_yes(self, tmp_path: Path) -> None:
        setup_writing_project(tmp_path, total_chapters=5)
        runner = CliRunner()
        result = runner.invoke(app, ["rollback", "-d", str(tmp_path), "-c", "3", "-y"])
        assert result.exit_code == 0
        assert "回滚成功" in result.output
        assert "ch003.md" in result.output

    def test_rollback_cancel(self, tmp_path: Path) -> None:
        setup_writing_project(tmp_path, total_chapters=5)
        runner = CliRunner()
        result = runner.invoke(
            app, ["rollback", "-d", str(tmp_path), "-c", "3"], input="n\n"
        )
        assert result.exit_code == 0
        assert "已取消" in result.output
        # 章节仍在
        assert (tmp_path / "chapters" / "ch003.md").exists()

    def test_rollback_beyond_written(self, tmp_path: Path) -> None:
        setup_writing_project(tmp_path, total_chapters=3)
        runner = CliRunner()
        result = runner.invoke(app, ["rollback", "-d", str(tmp_path), "-c", "10", "-y"])
        assert result.exit_code == 0
        assert "无需回滚" in result.output


class TestResumeCLI:
    def test_resume_no_state_file(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["resume", "-d", str(tmp_path)])
        assert result.exit_code == 1
        assert "状态文件不存在" in result.output

    def test_resume_basic(self, tmp_path: Path) -> None:
        setup_writing_project(tmp_path, total_chapters=5, state=State.PAUSED)
        runner = CliRunner()
        result = runner.invoke(app, ["resume", "-d", str(tmp_path)])
        assert result.exit_code == 0
        assert "续作简报" in result.output
        assert "第 5 章" in result.output
        assert "S01_悟道" in result.output

    def test_resume_with_save(self, tmp_path: Path) -> None:
        setup_writing_project(tmp_path, total_chapters=5, state=State.PAUSED)
        runner = CliRunner()
        result = runner.invoke(app, ["resume", "-d", str(tmp_path), "--save"])
        assert result.exit_code == 0
        assert (tmp_path / "resume_brief.md").exists()
        content = (tmp_path / "resume_brief.md").read_text(encoding="utf-8")
        assert "续作简报" in content
