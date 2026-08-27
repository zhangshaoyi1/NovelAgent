"""M11 导入导出单元测试

覆盖：
- ExportWorkflow：txt/markdown/epub 三格式、空章节、书名读取、自定义输出目录
- ImportWorkflow：草稿解析、world.md 生成、角色档案生成、文件不存在、LLM 解析失败
- CompletionExtrasWorkflow：感言/人物志/世界观/伏笔报告
- CLI 命令注册
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import frontmatter
import pytest

from agent.client import LLMResponse
from agent.workflows.m11_export import (
    CompletionExtrasWorkflow,
    ExportResult,
    ExportWorkflow,
    ImportResult,
    ImportWorkflow,
    CompletionExtras,
)


# ============================================================
# 夹具
# ============================================================
def _make_chapter(num: int, title: str, text: str) -> str:
    """生成带 frontmatter 的章节文件内容"""
    post = frontmatter.Post(text, chapter_title=title, chapter_num=num)
    return frontmatter.dumps(post)


@pytest.fixture
def project_with_chapters(tmp_path: Path) -> Path:
    """构造带 3 章的项目"""
    d = tmp_path / "p"
    d.mkdir(parents=True)
    chapters_dir = d / "chapters"
    chapters_dir.mkdir()

    (chapters_dir / "ch001.md").write_text(
        _make_chapter(1, "第一章 觉醒", "林寻睁开双眼，发现自己身处一处陌生石室。\n\n太虚镜在脑海中嗡鸣。"),
        encoding="utf-8",
    )
    (chapters_dir / "ch002.md").write_text(
        _make_chapter(2, "第二章 入门", "宗门长老审视着林寻，目光如电。\n\n\"你从何而来？\""),
        encoding="utf-8",
    )
    (chapters_dir / "ch003.md").write_text(
        _make_chapter(3, "第三章 试炼", "试炼场中，林寻一拳轰出。\n\n空气炸裂。"),
        encoding="utf-8",
    )

    # world.md 提供书名
    world = frontmatter.Post("# 总设定集\n\n## 简介\n\n修仙路。", title="太虚镜")
    (d / "world.md").write_text(frontmatter.dumps(world), encoding="utf-8")

    return d


@pytest.fixture
def empty_project(tmp_path: Path) -> Path:
    """空项目（无章节）"""
    d = tmp_path / "p"
    d.mkdir(parents=True)
    return d


# ============================================================
# ExportWorkflow
# ============================================================
class TestExportWorkflow:
    def test_export_txt(self, project_with_chapters: Path) -> None:
        wf = ExportWorkflow(project_with_chapters)
        result = wf.export("txt")

        assert result.success
        assert result.format == "txt"
        assert result.chapter_count == 3
        assert result.total_words > 0
        assert result.output_file.exists()
        assert result.output_file.suffix == ".txt"
        content = result.output_file.read_text(encoding="utf-8")
        assert "太虚镜" in content
        assert "第一章 觉醒" in content

    def test_export_markdown(self, project_with_chapters: Path) -> None:
        wf = ExportWorkflow(project_with_chapters)
        result = wf.export("markdown")

        assert result.success
        assert result.format == "markdown"
        assert result.chapter_count == 3
        content = result.output_file.read_text(encoding="utf-8")
        assert content.startswith("# 太虚镜")
        assert "## 第一章 觉醒" in content
        assert "## 第二章 入门" in content

    def test_export_markdown_alias_md(self, project_with_chapters: Path) -> None:
        wf = ExportWorkflow(project_with_chapters)
        result = wf.export("md")
        assert result.format == "markdown"
        assert result.success

    def test_export_with_custom_title(self, project_with_chapters: Path) -> None:
        wf = ExportWorkflow(project_with_chapters)
        result = wf.export("txt", title="我的修仙路")
        assert result.success
        assert "我的修仙路" in result.output_file.name
        content = result.output_file.read_text(encoding="utf-8")
        assert content.startswith("我的修仙路")

    def test_export_with_custom_output_dir(
        self, project_with_chapters: Path, tmp_path: Path
    ) -> None:
        wf = ExportWorkflow(project_with_chapters)
        out_dir = tmp_path / "custom_out"
        result = wf.export("markdown", output_dir=out_dir)
        assert result.success
        assert result.output_file.parent == out_dir
        assert out_dir.exists()

    def test_export_empty_project(self, empty_project: Path) -> None:
        wf = ExportWorkflow(empty_project)
        result = wf.export("txt")
        assert not result.success
        assert result.chapter_count == 0
        assert "无可导出" in result.message

    def test_export_unsupported_format(self, project_with_chapters: Path) -> None:
        wf = ExportWorkflow(project_with_chapters)
        with pytest.raises(ValueError, match="不支持"):
            wf.export("pdf")

    def test_export_fallback_title_when_no_world(
        self, empty_project: Path
    ) -> None:
        """无 world.md 时回退为"未命名小说" """
        # 给一个空 chapter 目录但有章节文件
        (empty_project / "chapters").mkdir()
        (empty_project / "chapters" / "ch001.md").write_text(
            _make_chapter(1, "第一章", "正文内容。"), encoding="utf-8"
        )
        wf = ExportWorkflow(empty_project)
        result = wf.export("txt")
        assert result.success
        assert "未命名小说" in result.output_file.name

    def test_sanitize_filename(self) -> None:
        assert ExportWorkflow._sanitize_filename("a/b:c?d") == "a_b_c_d"

    def test_parse_chapter_num(self) -> None:
        assert ExportWorkflow._parse_chapter_num("ch003.md") == 3
        assert ExportWorkflow._parse_chapter_num("readme.md") is None

    def test_markdown_to_html(self) -> None:
        html = ExportWorkflow._markdown_to_html("## 标题\n\n正文段落", "章题")
        assert "<h1>章题</h1>" in html
        assert "<h2>标题</h2>" in html
        assert "<p>正文段落</p>" in html


# ============================================================
# ImportWorkflow
# ============================================================
class TestImportWorkflow:
    def _make_llm(
        self,
        text: str = "",
        raise_exc: Exception | None = None,
    ) -> MagicMock:
        llm = MagicMock()
        if raise_exc:
            llm.chat_utility.side_effect = raise_exc
        else:
            llm.chat_utility.return_value = LLMResponse(text=text, raw={}, usage={})
        return llm

    def test_import_success(self, tmp_path: Path) -> None:
        d = tmp_path / "p"
        d.mkdir()
        src = tmp_path / "draft.txt"
        src.write_text("第一章 觉醒\n林寻觉醒太虚镜。", encoding="utf-8")

        llm_resp = json.dumps(
            {
                "title": "太虚镜",
                "genre": "xiuxian",
                "synopsis": "少年林寻觉醒太虚镜的修仙故事。",
                "worldview": "九霄大陆，灵气复苏。",
                "power_system": "炼气→筑基→金丹",
                "main_characters": [
                    {
                        "name": "林寻",
                        "role": "protagonist",
                        "identity": "少年",
                        "core_motivation": "寻找身世",
                    },
                    {
                        "name": "陈默",
                        "role": "supporting",
                        "identity": "挚友",
                        "core_motivation": "守护林寻",
                    },
                ],
                "chapter_count": 5,
            },
            ensure_ascii=False,
        )
        llm = self._make_llm(llm_resp)

        wf = ImportWorkflow(d, llm=llm)
        result = wf.import_draft(src)

        assert result.success
        assert result.detected_title == "太虚镜"
        assert result.detected_chapters == 5
        assert result.world_file is not None
        assert result.world_file.exists()
        assert len(result.character_files) == 2

        # 校验 world.md
        post = frontmatter.load(result.world_file)
        assert post.metadata["title"] == "太虚镜"
        assert post.metadata["genre"] == "xiuxian"
        assert post.metadata["imported_from"] == "draft.txt"
        assert "修仙故事" in post.content

        # 校验角色档案
        char_dir = d / "characters"
        assert (char_dir / "林寻.md").exists()
        assert (char_dir / "陈默.md").exists()
        char_post = frontmatter.load(char_dir / "林寻.md")
        assert char_post.metadata["role"] == "protagonist"
        assert "寻找身世" in char_post.content

    def test_import_no_characters(self, tmp_path: Path) -> None:
        d = tmp_path / "p"
        d.mkdir()
        src = tmp_path / "draft.md"
        src.write_text("# 我的小说\n正文。", encoding="utf-8")

        llm_resp = json.dumps(
            {
                "title": "我的小说",
                "main_characters": [
                    {"name": "主角", "role": "protagonist", "identity": "", "core_motivation": ""}
                ],
                "chapter_count": 1,
            },
            ensure_ascii=False,
        )
        llm = self._make_llm(llm_resp)
        wf = ImportWorkflow(d, llm=llm)
        result = wf.import_draft(src, with_characters=False)

        assert result.success
        assert result.world_file.exists()
        assert result.character_files == []
        assert not (d / "characters").exists()

    def test_import_file_not_exists(self, tmp_path: Path) -> None:
        d = tmp_path / "p"
        d.mkdir()
        llm = self._make_llm("{}")
        wf = ImportWorkflow(d, llm=llm)
        result = wf.import_draft(tmp_path / "missing.txt")

        assert not result.success
        assert "文件不存在" in result.message

    def test_import_llm_parse_failure(self, tmp_path: Path) -> None:
        d = tmp_path / "p"
        d.mkdir()
        src = tmp_path / "draft.txt"
        src.write_text("内容", encoding="utf-8")

        # 非 JSON 文本
        llm = self._make_llm("这不是 JSON")
        wf = ImportWorkflow(d, llm=llm)
        result = wf.import_draft(src)

        assert not result.success
        assert "解析失败" in result.message

    def test_import_truncates_long_text(self, tmp_path: Path) -> None:
        d = tmp_path / "p"
        d.mkdir()
        src = tmp_path / "draft.txt"
        src.write_text("x" * 20000, encoding="utf-8")

        llm_resp = json.dumps({"title": "T", "chapter_count": 0}, ensure_ascii=False)
        llm = self._make_llm(llm_resp)
        wf = ImportWorkflow(d, llm=llm)
        result = wf.import_draft(src)

        assert result.success
        # 验证 LLM 收到的是截断后的文本
        called_args = llm.chat_utility.call_args
        user_msg = called_args.kwargs["messages"][1]["content"]
        assert len(user_msg) < 20000

    def test_import_missing_optional_fields(self, tmp_path: Path) -> None:
        """LLM 只返回 title，其他字段缺失应正常处理"""
        d = tmp_path / "p"
        d.mkdir()
        src = tmp_path / "draft.txt"
        src.write_text("草稿", encoding="utf-8")

        llm = self._make_llm(json.dumps({"title": "只有标题"}, ensure_ascii=False))
        wf = ImportWorkflow(d, llm=llm)
        result = wf.import_draft(src)

        assert result.success
        assert result.detected_title == "只有标题"
        assert result.detected_chapters == 0
        assert result.character_files == []

    def test_import_default_title_from_filename(self, tmp_path: Path) -> None:
        """LLM 未返回 title 时用文件名"""
        d = tmp_path / "p"
        d.mkdir()
        src = tmp_path / "我的草稿.txt"
        src.write_text("内容", encoding="utf-8")

        llm = self._make_llm(json.dumps({"chapter_count": 0}, ensure_ascii=False))
        wf = ImportWorkflow(d, llm=llm)
        result = wf.import_draft(src)

        assert result.success
        assert result.detected_title == "我的草稿"


# ============================================================
# CompletionExtrasWorkflow
# ============================================================
class TestCompletionExtrasWorkflow:
    @pytest.fixture
    def project(self, tmp_path: Path) -> Path:
        d = tmp_path / "p"
        d.mkdir(parents=True)

        # world.md
        world = frontmatter.Post(
            "# 总设定集\n\n## 简介\n\n少年林寻的修仙路。",
            title="太虚镜",
            genre="xiuxian",
        )
        (d / "world.md").write_text(frontmatter.dumps(world), encoding="utf-8")

        # 角色档案
        char_dir = d / "characters"
        char_dir.mkdir()
        char1 = frontmatter.Post(
            "# 林寻\n\n少年。", name="林寻", role="protagonist"
        )
        (char_dir / "林寻.md").write_text(frontmatter.dumps(char1), encoding="utf-8")
        char2 = frontmatter.Post(
            "# 陈默\n\n挚友。", name="陈默", role="supporting"
        )
        (char_dir / "陈默.md").write_text(frontmatter.dumps(char2), encoding="utf-8")

        # 伏笔表（让 M13 报告可生成）
        (d / "foreshadows.md").write_text(
            "# 伏笔登记表\n\n| ID | 伏笔内容 | 埋设位置 | 预期回收点 | 状态 | 关联角色 |\n|---|---|---|---|---|---|\n| F-01 | 测试 | ch001 | ch010 | 已埋 | 林寻 |\n",
            encoding="utf-8",
        )
        return d

    def test_generate_all(self, project: Path) -> None:
        llm = MagicMock()
        llm.chat_utility.return_value = LLMResponse(
            text="感谢读者陪伴，这段旅程...", raw={}, usage={}
        )
        wf = CompletionExtrasWorkflow(project, llm=llm)
        result = wf.generate()

        assert isinstance(result, CompletionExtras)
        assert result.afterword_file is not None
        assert result.afterword_file.exists()
        assert "感谢读者" in result.afterword_file.read_text(encoding="utf-8")

        assert result.character_anthology_file is not None
        assert result.character_anthology_file.exists()
        anthology = result.character_anthology_file.read_text(encoding="utf-8")
        assert "林寻" in anthology
        assert "陈默" in anthology

        assert result.world_summary_file is not None
        assert result.world_summary_file.exists()

        assert result.foreshadow_report_file is not None
        assert result.foreshadow_report_file.exists()

        assert "完本感言" in result.message

    def test_generate_skip_afterword(self, project: Path) -> None:
        llm = MagicMock()
        wf = CompletionExtrasWorkflow(project, llm=llm)
        result = wf.generate(skip_afterword=True)

        assert result.afterword_file is None
        # 不应调用 LLM
        llm.chat_utility.assert_not_called()
        # 其他产出仍应有
        assert result.character_anthology_file is not None

    def test_generate_custom_output_dir(
        self, project: Path, tmp_path: Path
    ) -> None:
        llm = MagicMock()
        llm.chat_utility.return_value = LLMResponse(text="感言", raw={}, usage={})
        wf = CompletionExtrasWorkflow(project, llm=llm)
        out_dir = tmp_path / "extras"
        result = wf.generate(output_dir=out_dir)

        assert out_dir.exists()
        assert result.character_anthology_file.parent == out_dir

    def test_generate_no_world(self, tmp_path: Path) -> None:
        d = tmp_path / "p"
        d.mkdir()
        llm = MagicMock()
        wf = CompletionExtrasWorkflow(d, llm=llm)
        result = wf.generate()

        # 没感言、没世界观、没人物志，只有 message
        assert result.afterword_file is None
        assert result.world_summary_file is None
        assert result.character_anthology_file is None

    def test_generate_no_characters(self, tmp_path: Path) -> None:
        d = tmp_path / "p"
        d.mkdir()
        world = frontmatter.Post("# 总设定集", title="T")
        (d / "world.md").write_text(frontmatter.dumps(world), encoding="utf-8")

        llm = MagicMock()
        llm.chat_utility.return_value = LLMResponse(text="感言", raw={}, usage={})
        wf = CompletionExtrasWorkflow(d, llm=llm)
        result = wf.generate()

        assert result.afterword_file is not None
        assert result.character_anthology_file is None
        assert result.world_summary_file is not None

    def test_afterword_llm_failure_falls_back(self, project: Path) -> None:
        """LLM 调用失败时，感言为 None 但其他产出正常"""
        llm = MagicMock()
        llm.chat_utility.side_effect = Exception("network error")
        wf = CompletionExtrasWorkflow(project, llm=llm)
        result = wf.generate()

        assert result.afterword_file is None
        assert result.character_anthology_file is not None


# ============================================================
# CLI 命令注册
# ============================================================
class TestCLI:
    def test_cli_commands_registered(self) -> None:
        from agent.cli import app

        # Typer 的 registered_commands 属性 - 名称取 name 或 callback 函数名
        names = {c.name or c.callback.__name__ for c in app.registered_commands}
        assert "export" in names
        assert "import_draft" in names  # typer 自动转为 import-draft
        assert "completion_extras" in names
