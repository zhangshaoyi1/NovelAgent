"""M12 内容审核与上下文管理单元测试

覆盖：
- ConflictArbiter (F12.1)：设定冲突检测、报告解析、严重度判定
- ContentAuditor (F12.2)：内容审核、违规解析、拦截判定、策略切换
- ChapterSummarizer (F12.3)：章节摘要生成、持久化、批量、回退
- ContextLoader (F12.3)：必载层 + 按需层 + 完整上下文
- CLI 命令注册
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import frontmatter
import pytest

from agent.client import LLMResponse
from agent.workflows.m12_audit import (
    ChapterSummary,
    ChapterSummarizer,
    Conflict,
    ConflictArbiter,
    ConflictReport,
    ContentAuditor,
    ContextLoader,
    AuditResult,
    Violation,
    VIOLENCE_POLICIES,
)


# ============================================================
# 夹具
# ============================================================
@pytest.fixture
def project(tmp_path: Path) -> Path:
    """构造带基础设定的项目"""
    d = tmp_path / "p"
    d.mkdir(parents=True)

    # world.md
    world = frontmatter.Post(
        "# 总设定集\n\n## 故事简介\n\n少年林寻的修仙路。\n\n## 境界体系\n\n炼气→筑基→金丹→元婴",
        title="太虚镜",
        genre="xiuxian",
        scope="long",
        style={"tone": "热血", "pov": "第三人称限制", "rhythm": "快", "chapter_length": 3000},
    )
    (d / "world.md").write_text(frontmatter.dumps(world), encoding="utf-8")

    # 角色档案
    char_dir = d / "characters"
    char_dir.mkdir()
    char1 = frontmatter.Post(
        "# 林寻\n\n主角，少年，太虚镜宿主。",
        name="林寻",
        role="protagonist",
        identity="少年",
    )
    (char_dir / "林寻.md").write_text(frontmatter.dumps(char1), encoding="utf-8")

    # 支线
    subline_dir = d / "sublines" / "S01_觉醒"
    subline_dir.mkdir(parents=True)
    subline = frontmatter.Post(
        "# S01 觉醒\n\n## 支线目标\n\n林寻觉醒太虚镜。",
        subline_id="S01",
        subline_name="觉醒",
    )
    (subline_dir / "subline.md").write_text(frontmatter.dumps(subline), encoding="utf-8")

    return d


@pytest.fixture
def project_with_chapters(project: Path) -> Path:
    """在 project 基础上添加章节"""
    chapters_dir = project / "chapters"
    chapters_dir.mkdir(exist_ok=True)
    for i in range(1, 4):
        post = frontmatter.Post(
            f"第{i}章正文内容，林寻继续修炼。这一章发生了一些事件。",
            chapter_title=f"第{i}章 测试",
            chapter_num=i,
        )
        (chapters_dir / f"ch{i:03d}.md").write_text(
            frontmatter.dumps(post), encoding="utf-8"
        )
    return project


def _make_llm(text: str = "") -> MagicMock:
    llm = MagicMock()
    llm.chat_utility.return_value = LLMResponse(text=text, raw={}, usage={})
    return llm


# ============================================================
# Conflict / ConflictReport 数据类
# ============================================================
class TestConflictReport:
    def test_empty_report(self) -> None:
        report = ConflictReport(conflicts=[], summary="无冲突")
        assert not report.has_conflict
        assert report.high_severity_count == 0
        assert not report.needs_arbitration

    def test_with_low_severity(self) -> None:
        report = ConflictReport(
            conflicts=[
                Conflict("x", "old", "new", "low", [], "ignore"),
                Conflict("y", "old", "new", "medium", [1, 2], "merge"),
            ],
            summary="2 条冲突",
        )
        assert report.has_conflict
        assert report.high_severity_count == 0
        assert not report.needs_arbitration

    def test_needs_arbitration(self) -> None:
        report = ConflictReport(
            conflicts=[
                Conflict("x", "old", "new", "high", [5, 8], "rewrite"),
            ],
            summary="1 条 high 冲突",
        )
        assert report.needs_arbitration
        assert report.high_severity_count == 1

    def test_to_dict(self) -> None:
        report = ConflictReport(
            conflicts=[Conflict("x", "old", "new", "high", [1], "fix")],
            summary="s",
        )
        d = report.to_dict()
        assert d["summary"] == "s"
        assert d["conflicts"][0]["field"] == "x"
        assert d["conflicts"][0]["severity"] == "high"


# ============================================================
# ConflictArbiter (F12.1)
# ============================================================
class TestConflictArbiter:
    def test_no_conflict(self, project: Path) -> None:
        llm = _make_llm(
            json.dumps({"conflicts": [], "summary": "无冲突"}, ensure_ascii=False)
        )
        arbiter = ConflictArbiter(project, llm=llm)
        report = arbiter.check_new_setting("林寻在森林中遇到一只白狐")

        assert not report.has_conflict
        assert "无冲突" in report.summary

    def test_detects_conflict(self, project: Path) -> None:
        llm_resp = json.dumps(
            {
                "conflicts": [
                    {
                        "field": "境界体系",
                        "existing": "炼气→筑基→金丹",
                        "new": "直接跳到金丹",
                        "severity": "high",
                        "affected_chapters": [5, 8],
                        "suggestion": "重写第5、8章",
                    }
                ],
                "summary": "境界跳跃冲突",
            },
            ensure_ascii=False,
        )
        llm = _make_llm(llm_resp)
        arbiter = ConflictArbiter(project, llm=llm)
        report = arbiter.check_new_setting("主角境界直接到金丹")

        assert report.has_conflict
        assert report.high_severity_count == 1
        assert report.needs_arbitration
        assert report.conflicts[0].field == "境界体系"
        assert report.conflicts[0].affected_chapters == [5, 8]

    def test_llm_parse_failure_returns_empty(self, project: Path) -> None:
        llm = _make_llm("这不是 JSON")
        arbiter = ConflictArbiter(project, llm=llm)
        report = arbiter.check_new_setting("新设定")

        assert not report.has_conflict
        assert "失败" in report.summary

    def test_llm_exception_returns_empty(self, project: Path) -> None:
        llm = MagicMock()
        llm.chat_utility.side_effect = Exception("network")
        arbiter = ConflictArbiter(project, llm=llm)
        report = arbiter.check_new_setting("新设定")

        assert not report.has_conflict
        assert "失败" in report.summary

    def test_with_subline_id(self, project: Path) -> None:
        llm = _make_llm(
            json.dumps({"conflicts": [], "summary": "无"}, ensure_ascii=False)
        )
        arbiter = ConflictArbiter(project, llm=llm)
        arbiter.check_new_setting("设定", subline_id="S01")
        # 验证 LLM 被调用
        llm.chat_utility.assert_called_once()

    def test_no_world_file(self, tmp_path: Path) -> None:
        d = tmp_path / "p"
        d.mkdir()
        llm = _make_llm(
            json.dumps({"conflicts": [], "summary": "无"}, ensure_ascii=False)
        )
        arbiter = ConflictArbiter(d, llm=llm)
        report = arbiter.check_new_setting("设定")
        # 应能正常运行（world 内容为"（无）"）
        assert not report.has_conflict


# ============================================================
# Violation / AuditResult 数据类
# ============================================================
class TestAuditResult:
    def test_passed(self) -> None:
        r = AuditResult(passed=True, violations=[], summary="通过")
        assert r.passed
        assert not r.needs_block

    def test_needs_block(self) -> None:
        r = AuditResult(
            passed=False,
            violations=[
                Violation("violence", "high", "片段", "原因", "建议"),
                Violation("sexual", "low", "片段", "原因"),
            ],
            summary="违规",
        )
        assert not r.passed
        assert r.high_severity_count == 1
        assert r.needs_block

    def test_no_block_when_only_low(self) -> None:
        r = AuditResult(
            passed=False,
            violations=[Violation("violence", "low", "x", "y")],
            summary="轻微",
        )
        assert not r.needs_block


# ============================================================
# ContentAuditor (F12.2)
# ============================================================
class TestContentAuditor:
    def test_pass(self, project: Path) -> None:
        llm = _make_llm(
            json.dumps(
                {"passed": True, "violations": [], "summary": "无违规"},
                ensure_ascii=False,
            )
        )
        auditor = ContentAuditor(project, llm=llm)
        result = auditor.audit_chapter("林寻一拳击出，空气炸裂。")

        assert result.passed
        assert not result.needs_block

    def test_detect_violation(self, project: Path) -> None:
        llm_resp = json.dumps(
            {
                "passed": False,
                "violations": [
                    {
                        "type": "violence",
                        "severity": "high",
                        "excerpt": "血肉横飞...",
                        "reason": "过度血腥",
                        "suggestion": "淡化处理",
                    }
                ],
                "summary": "极端暴力",
            },
            ensure_ascii=False,
        )
        llm = _make_llm(llm_resp)
        auditor = ContentAuditor(project, llm=llm)
        result = auditor.audit_chapter("血肉横飞的描写...")

        assert not result.passed
        assert result.needs_block
        assert result.violations[0].type == "violence"
        assert result.violations[0].severity == "high"

    def test_llm_failure_defaults_pass(self, project: Path) -> None:
        """LLM 异常时默认放行（避免阻塞写作）"""
        llm = MagicMock()
        llm.chat_utility.side_effect = Exception("network")
        auditor = ContentAuditor(project, llm=llm)
        result = auditor.audit_chapter("正文")

        assert result.passed
        assert "失败" in result.summary

    def test_llm_parse_failure_defaults_pass(self, project: Path) -> None:
        llm = _make_llm("非 JSON")
        auditor = ContentAuditor(project, llm=llm)
        result = auditor.audit_chapter("正文")

        assert result.passed

    def test_violence_policies(self) -> None:
        assert "lenient" in VIOLENCE_POLICIES
        assert "standard" in VIOLENCE_POLICIES
        assert "strict" in VIOLENCE_POLICIES
        assert "宽松" in VIOLENCE_POLICIES["lenient"]
        assert "严格" in VIOLENCE_POLICIES["strict"]

    def test_custom_policy(self, project: Path) -> None:
        llm = _make_llm(
            json.dumps({"passed": True, "violations": []}, ensure_ascii=False)
        )
        auditor = ContentAuditor(project, llm=llm, violence_policy="strict")
        result = auditor.audit_chapter("正文", violence_policy="lenient")
        # 验证传给 LLM 的内容包含 lenient 描述
        call_args = llm.chat_utility.call_args
        user_msg = call_args.kwargs["messages"][1]["content"]
        assert "宽松" in user_msg

    def test_long_text_truncated(self, project: Path) -> None:
        llm = _make_llm(
            json.dumps({"passed": True, "violations": []}, ensure_ascii=False)
        )
        auditor = ContentAuditor(project, llm=llm)
        long_text = "x" * 10000
        auditor.audit_chapter(long_text)
        call_args = llm.chat_utility.call_args
        user_msg = call_args.kwargs["messages"][1]["content"]
        assert len(user_msg) < 10000


# ============================================================
# ChapterSummary 数据类
# ============================================================
class TestChapterSummary:
    def test_to_markdown_minimal(self) -> None:
        s = ChapterSummary(chapter_num=1, title="测试", summary="摘要内容")
        md = s.to_markdown()
        assert "### 第 1 章 测试" in md
        assert "摘要内容" in md

    def test_to_markdown_full(self) -> None:
        s = ChapterSummary(
            chapter_num=5,
            title="觉醒",
            summary="林寻觉醒太虚镜。",
            key_events=["觉醒", "拜师"],
            character_changes=[{"name": "林寻", "change": "获得金手指"}],
            new_settings=["太虚镜"],
            foreshadows=["F-01 太虚镜情感乱码"],
        )
        md = s.to_markdown()
        assert "**关键事件：**" in md
        assert "- 觉醒" in md
        assert "**角色变化：**" in md
        assert "林寻" in md
        assert "**新设定：**" in md
        assert "太虚镜" in md
        assert "**伏笔：**" in md

    def test_to_dict(self) -> None:
        s = ChapterSummary(
            chapter_num=2,
            title="T",
            summary="S",
            key_events=["E"],
        )
        d = s.to_dict()
        assert d["chapter_num"] == 2
        assert d["key_events"] == ["E"]


# ============================================================
# ChapterSummarizer (F12.3)
# ============================================================
class TestChapterSummarizer:
    def test_summarize_chapter_success(self, project_with_chapters: Path) -> None:
        llm_resp = json.dumps(
            {
                "chapter_num": 1,
                "title": "第一章 测试",
                "summary": "林寻继续修炼。",
                "key_events": ["修炼", "突破"],
                "character_changes": [{"name": "林寻", "change": "境界提升"}],
                "new_settings": ["新功法"],
                "foreshadows": [],
            },
            ensure_ascii=False,
        )
        llm = _make_llm(llm_resp)
        summarizer = ChapterSummarizer(project_with_chapters, llm=llm)
        summary = summarizer.summarize_chapter(1)

        assert summary is not None
        assert summary.chapter_num == 1
        assert summary.title == "第一章 测试"
        assert "修炼" in summary.key_events
        assert summary.character_changes[0]["name"] == "林寻"

        # 验证持久化
        summary_file = (
            project_with_chapters / "chapters" / "_summaries" / "ch001.json"
        )
        assert summary_file.exists()
        saved = json.loads(summary_file.read_text(encoding="utf-8"))
        assert saved["chapter_num"] == 1

    def test_summarize_chapter_not_exists(self, project: Path) -> None:
        llm = _make_llm("{}")
        summarizer = ChapterSummarizer(project, llm=llm)
        assert summarizer.summarize_chapter(99) is None

    def test_summarize_llm_failure(self, project_with_chapters: Path) -> None:
        llm = MagicMock()
        llm.chat_utility.side_effect = Exception("err")
        summarizer = ChapterSummarizer(project_with_chapters, llm=llm)
        assert summarizer.summarize_chapter(1) is None

    def test_summarize_llm_parse_failure(self, project_with_chapters: Path) -> None:
        llm = _make_llm("not json")
        summarizer = ChapterSummarizer(project_with_chapters, llm=llm)
        assert summarizer.summarize_chapter(1) is None

    def test_load_summary(self, project_with_chapters: Path) -> None:
        # 先生成
        llm_resp = json.dumps(
            {
                "chapter_num": 2,
                "title": "第二章",
                "summary": "摘要",
                "key_events": [],
                "character_changes": [],
                "new_settings": [],
                "foreshadows": [],
            },
            ensure_ascii=False,
        )
        llm = _make_llm(llm_resp)
        summarizer = ChapterSummarizer(project_with_chapters, llm=llm)
        summarizer.summarize_chapter(2)

        # 加载
        loaded = summarizer.load_summary(2)
        assert loaded is not None
        assert loaded.chapter_num == 2
        assert loaded.title == "第二章"

    def test_load_summary_not_exists(self, project: Path) -> None:
        summarizer = ChapterSummarizer(project, llm=_make_llm("{}"))
        assert summarizer.load_summary(99) is None

    def test_list_summaries(self, project_with_chapters: Path) -> None:
        llm = _make_llm(
            json.dumps(
                {"chapter_num": 1, "title": "T", "summary": "S"}, ensure_ascii=False
            )
        )
        summarizer = ChapterSummarizer(project_with_chapters, llm=llm)
        summarizer.summarize_chapter(1)
        summarizer.summarize_chapter(3)
        nums = summarizer.list_summaries()
        assert nums == [1, 3]

    def test_list_summaries_empty(self, project: Path) -> None:
        summarizer = ChapterSummarizer(project, llm=_make_llm("{}"))
        assert summarizer.list_summaries() == []

    def test_summarize_range_skip_existing(self, project_with_chapters: Path) -> None:
        # 先生成 ch1
        llm_resp = json.dumps(
            {"chapter_num": 1, "title": "T1", "summary": "S1"}, ensure_ascii=False
        )
        llm = _make_llm(llm_resp)
        summarizer = ChapterSummarizer(project_with_chapters, llm=llm)
        summarizer.summarize_chapter(1)

        # 批量生成 1-3（跳过已存在）
        llm_resp2 = json.dumps(
            {"chapter_num": 2, "title": "T2", "summary": "S2"}, ensure_ascii=False
        )
        llm2 = _make_llm(llm_resp2)
        summarizer2 = ChapterSummarizer(project_with_chapters, llm=llm2)
        results = summarizer2.summarize_range(1, 3, skip_existing=True)
        # 只生成 2 和 3（1 已存在）
        nums = [r.chapter_num for r in results]
        assert 1 not in nums
        assert 2 in nums

    def test_summarize_range_force(self, project_with_chapters: Path) -> None:
        llm = _make_llm(
            json.dumps(
                {"chapter_num": 1, "title": "T1", "summary": "S1"}, ensure_ascii=False
            )
        )
        summarizer = ChapterSummarizer(project_with_chapters, llm=llm)
        summarizer.summarize_chapter(1)

        # 强制重新生成
        llm2 = _make_llm(
            json.dumps(
                {"chapter_num": 1, "title": "T1_new", "summary": "S1_new"},
                ensure_ascii=False,
            )
        )
        summarizer2 = ChapterSummarizer(project_with_chapters, llm=llm2)
        results = summarizer2.summarize_range(1, 1, skip_existing=False)
        assert len(results) == 1
        assert results[0].title == "T1_new"

    def test_compile_history_brief(self, project_with_chapters: Path) -> None:
        # 生成 ch1 和 ch2 的摘要
        llm = _make_llm(
            json.dumps(
                {
                    "chapter_num": 1,
                    "title": "T1",
                    "summary": "S1",
                    "key_events": ["E1"],
                },
                ensure_ascii=False,
            )
        )
        summarizer = ChapterSummarizer(project_with_chapters, llm=llm)
        summarizer.summarize_chapter(1)
        summarizer.summarize_chapter(2)

        brief = summarizer.compile_history_brief(up_to_chapter=3)
        assert "前情简报" in brief
        assert "T1" in brief

    def test_compile_history_empty(self, project: Path) -> None:
        summarizer = ChapterSummarizer(project, llm=_make_llm("{}"))
        brief = summarizer.compile_history_brief(up_to_chapter=5)
        assert "暂无" in brief


# ============================================================
# ContextLoader (F12.3)
# ============================================================
class TestContextLoader:
    def test_load_essential(self, project: Path) -> None:
        loader = ContextLoader(project, llm=_make_llm("{}"))
        ctx = loader.load_essential(chapter_num=5)

        assert ctx["layer"] == "essential"
        assert ctx["chapter_num"] == 5
        assert ctx["subline_id"] == "S01_觉醒"  # list_sublines 返回目录名
        assert ctx["subline_name"] == "觉醒"
        assert "太虚镜" in ctx["world_summary"]
        assert "境界体系" in ctx["world_summary"]
        assert len(ctx["characters"]) == 1
        assert ctx["characters"][0]["name"] == "林寻"

    def test_load_essential_with_character_filter(self, project: Path) -> None:
        loader = ContextLoader(project, llm=_make_llm("{}"))
        ctx = loader.load_essential(chapter_num=1, character_names=["林寻"])
        assert len(ctx["characters"]) == 1

    def test_load_essential_with_unknown_character(self, project: Path) -> None:
        loader = ContextLoader(project, llm=_make_llm("{}"))
        ctx = loader.load_essential(chapter_num=1, character_names=["不存在"])
        assert ctx["characters"] == []

    def test_load_essential_no_world(self, tmp_path: Path) -> None:
        d = tmp_path / "p"
        d.mkdir()
        loader = ContextLoader(d, llm=_make_llm("{}"))
        ctx = loader.load_essential(chapter_num=1)
        assert ctx["world_summary"] == ""

    def test_load_on_demand(self, project_with_chapters: Path) -> None:
        loader = ContextLoader(project_with_chapters, llm=_make_llm("{}"))
        od = loader.load_on_demand(chapter_num=3)

        assert od["layer"] == "on_demand"
        assert "history" in od
        # 没有摘要时回退到最近章节
        assert "最近章节片段" in od["history"] or "前情简报" in od["history"]

    def test_load_on_demand_with_summaries(self, project_with_chapters: Path) -> None:
        # 先生成摘要
        llm = _make_llm(
            json.dumps(
                {
                    "chapter_num": 1,
                    "title": "T1",
                    "summary": "S1",
                    "key_events": ["E1"],
                },
                ensure_ascii=False,
            )
        )
        summarizer = ChapterSummarizer(project_with_chapters, llm=llm)
        summarizer.summarize_chapter(1)
        summarizer.summarize_chapter(2)

        loader = ContextLoader(project_with_chapters, llm=_make_llm("{}"))
        od = loader.load_on_demand(chapter_num=3)
        assert "前情简报" in od["history"]
        assert "T1" in od["history"]

    def test_load_on_demand_with_foreshadows(self, project_with_chapters: Path) -> None:
        # 添加伏笔表
        (project_with_chapters / "foreshadows.md").write_text(
            "# 伏笔表\n| F-01 | 测试 | ch001 | ch010 | 已埋 | 林寻 |",
            encoding="utf-8",
        )
        loader = ContextLoader(project_with_chapters, llm=_make_llm("{}"))
        od = loader.load_on_demand(chapter_num=3, include_foreshadows=True)
        assert "foreshadows" in od
        assert "F-01" in od["foreshadows"]

    def test_load_on_demand_with_other_sublines(self, project: Path) -> None:
        # 添加第二个支线
        sub2 = project / "sublines" / "S02_试炼"
        sub2.mkdir(parents=True)
        post = frontmatter.Post("# S02 试炼", subline_id="S02", subline_name="试炼")
        (sub2 / "subline.md").write_text(frontmatter.dumps(post), encoding="utf-8")

        loader = ContextLoader(project, llm=_make_llm("{}"))
        od = loader.load_on_demand(chapter_num=1, include_other_sublines=True)
        assert "other_sublines" in od
        assert len(od["other_sublines"]) >= 1

    def test_load_on_demand_exclude_options(self, project_with_chapters: Path) -> None:
        loader = ContextLoader(project_with_chapters, llm=_make_llm("{}"))
        od = loader.load_on_demand(
            chapter_num=3,
            include_history=False,
            include_other_sublines=False,
            include_foreshadows=False,
        )
        assert "history" not in od
        assert "other_sublines" not in od
        assert "foreshadows" not in od

    def test_load_full_context(self, project_with_chapters: Path) -> None:
        loader = ContextLoader(project_with_chapters, llm=_make_llm("{}"))
        ctx = loader.load_full_context(chapter_num=5)

        assert "essential" in ctx
        assert "on_demand" in ctx
        assert ctx["essential"]["chapter_num"] == 5

    def test_load_full_context_essential_only(self, project: Path) -> None:
        loader = ContextLoader(project, llm=_make_llm("{}"))
        ctx = loader.load_full_context(chapter_num=1, include_on_demand=False)
        # 不包含 on_demand
        assert "on_demand" not in ctx
        assert ctx["chapter_num"] == 1

    def test_load_relations_subgraph(self, project: Path) -> None:
        # 添加关系网
        rel_dir = project / "relations"
        rel_dir.mkdir()
        (rel_dir / "graph.md").write_text("# 关系网\n林寻→陈默:挚友", encoding="utf-8")

        loader = ContextLoader(project, llm=_make_llm("{}"))
        ctx = loader.load_essential(chapter_num=1)
        assert "林寻" in ctx["relations"]

    def test_extract_world_summary_no_style(self, tmp_path: Path) -> None:
        """无 style 字段的 world.md 也能正确提取摘要"""
        d = tmp_path / "p"
        d.mkdir()
        world = frontmatter.Post(
            "# 总设定集\n\n## 故事简介\n\n简单故事。",
            title="T",
            genre="xiuxian",
        )
        (d / "world.md").write_text(frontmatter.dumps(world), encoding="utf-8")

        loader = ContextLoader(d, llm=_make_llm("{}"))
        ctx = loader.load_essential(chapter_num=1)
        assert "T" in ctx["world_summary"]
        assert "简单故事" in ctx["world_summary"]


# ============================================================
# CLI 命令注册
# ============================================================
class TestCLI:
    def test_cli_commands_registered(self) -> None:
        from agent.cli import app

        names = {c.name or c.callback.__name__ for c in app.registered_commands}
        assert "audit_setting" in names
        assert "audit_chapter" in names
        assert "summarize_chapter" in names
        assert "summarize_range" in names
        assert "context" in names
