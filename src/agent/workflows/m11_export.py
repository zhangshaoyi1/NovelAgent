"""M11 导入导出工作流

基于 PRD F11.1-F11.3：

F11.1 导入
    - 用户上传已有大纲/草稿（txt/markdown）
    - Agent 反向解析并构建设定集、角色档案、剧集树（需用户确认）
    - 当前实现：基础导入，读取文本文件，用 LLM 提取世界观/角色/剧情摘要

F11.2 导出
    - 完结或中途导出为 txt / markdown / epub
    - txt：纯文本拼接
    - markdown：带标题层级
    - epub：需 ebooklib（可选依赖，未安装时降级提示）

F11.3 完本附加产出
    - 完本感言（LLM 生成）
    - 人物志（汇总 characters/*.md）
    - 世界观总结（汇总 world.md）
    - 伏笔回收报告（复用 M13）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import frontmatter
from rich.console import Console
from agent.core.workflow_registry import workflow
from rich.panel import Panel

from agent.client import LLMClient
from agent.core.setting_manager import SettingManager
from agent.core.state_machine import StateMachine
from agent.utils import parse_llm_json


# ============================================================
# F11.2 导出
# ============================================================
@dataclass
class ExportResult:
    """导出结果"""

    success: bool
    format: str  # txt | markdown | epub
    output_file: Path
    chapter_count: int
    total_words: int
    message: str = ""


@workflow("m11_export")
class ExportWorkflow:
    """导出工作流（F11.2）

    用法：
        wf = ExportWorkflow(project_dir)
        result = wf.export("markdown", output_dir=project_dir / "exports")
    """

    def __init__(
        self,
        project_dir: Path,
        console: Console | None = None,
    ) -> None:
        self.project_dir = Path(project_dir)
        self.console = console or Console()
        self.chapters_dir = self.project_dir / "chapters"

    def export(
        self,
        fmt: str,
        output_dir: Path | None = None,
        title: str | None = None,
    ) -> ExportResult:
        """导出全部章节

        Args:
            fmt: 格式 txt | markdown | epub
            output_dir: 输出目录，None 则用 project_dir/exports/
            title: 书名，None 则从 world.md 读取

        Returns:
            ExportResult
        """
        fmt = fmt.lower()
        if fmt not in ("txt", "markdown", "md", "epub"):
            raise ValueError(f"不支持的导出格式: {fmt}，可选: txt / markdown / epub")

        # 统一 markdown 别名
        if fmt == "md":
            fmt = "markdown"

        # 收集章节
        chapters = self._collect_chapters()
        if not chapters:
            return ExportResult(
                success=False,
                format=fmt,
                output_file=self.project_dir,
                chapter_count=0,
                total_words=0,
                message="无可导出的章节（chapters/ 目录为空）",
            )

        # 书名
        if title is None:
            title = self._get_book_title()

        # 输出目录
        if output_dir is None:
            output_dir = self.project_dir / "exports"
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # 按格式导出
        safe_title = self._sanitize_filename(title)
        if fmt == "txt":
            output_file = output_dir / f"{safe_title}.txt"
            word_count = self._export_txt(chapters, output_file, title)
        elif fmt == "markdown":
            output_file = output_dir / f"{safe_title}.md"
            word_count = self._export_markdown(chapters, output_file, title)
        elif fmt == "epub":
            output_file = output_dir / f"{safe_title}.epub"
            word_count = self._export_epub(chapters, output_file, title)
        else:  # pragma: no cover
            raise ValueError(f"不支持的格式: {fmt}")

        return ExportResult(
            success=True,
            format=fmt,
            output_file=output_file,
            chapter_count=len(chapters),
            total_words=word_count,
            message=f"已导出 {len(chapters)} 章 / {word_count} 字到 {output_file.name}",
        )

    def _collect_chapters(self) -> list[tuple[int, str, str]]:
        """收集所有章节，返回 [(chapter_num, title, text), ...]"""
        if not self.chapters_dir.exists():
            return []
        chapters: list[tuple[int, str, str]] = []
        for ch_file in sorted(self.chapters_dir.glob("ch*.md")):
            num = self._parse_chapter_num(ch_file.name)
            if num is None:
                continue
            post = frontmatter.load(ch_file)
            title = post.metadata.get("chapter_title", f"第{num}章")
            text = post.content
            chapters.append((num, str(title), text))
        return chapters

    @staticmethod
    def _parse_chapter_num(filename: str) -> int | None:
        m = re.match(r"ch(\d+)\.md$", filename)
        return int(m.group(1)) if m else None

    def _get_book_title(self) -> str:
        """从 world.md 读取书名"""
        world_file = self.project_dir / "world.md"
        if world_file.exists():
            post = frontmatter.load(world_file)
            title = post.metadata.get("title", "")
            if title:
                return str(title)
        return "未命名小说"

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        """文件名安全化"""
        return re.sub(r'[\\/:*?"<>|]', "_", name)

    def _export_txt(
        self,
        chapters: list[tuple[int, str, str]],
        output_file: Path,
        title: str,
    ) -> int:
        """导出为纯文本"""
        lines: list[str] = [title, "=" * 40, ""]
        total_words = 0
        for num, ch_title, text in chapters:
            lines.append(f"{ch_title}")
            lines.append("-" * 30)
            # 去掉 markdown 标记
            clean = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)
            lines.append(clean)
            lines.append("")
            total_words += len(clean.replace("\n", "").replace(" ", ""))
        output_file.write_text("\n".join(lines), encoding="utf-8")
        return total_words

    def _export_markdown(
        self,
        chapters: list[tuple[int, str, str]],
        output_file: Path,
        title: str,
    ) -> int:
        """导出为 Markdown"""
        lines: list[str] = [f"# {title}", ""]
        total_words = 0
        for num, ch_title, text in chapters:
            lines.append(f"## {ch_title}")
            lines.append("")
            lines.append(text)
            lines.append("")
            lines.append("---")
            lines.append("")
            total_words += len(text.replace("\n", "").replace(" ", ""))
        output_file.write_text("\n".join(lines), encoding="utf-8")
        return total_words

    def _export_epub(
        self,
        chapters: list[tuple[int, str, str]],
        output_file: Path,
        title: str,
    ) -> int:
        """导出为 EPUB（需 ebooklib）"""
        try:
            from ebooklib import epub
        except ImportError as e:
            raise ImportError(
                "导出 EPUB 需要安装 ebooklib：pip install ebooklib"
            ) from e

        book = epub.EpubBook()
        book.set_title(title)
        book.set_language("zh-CN")

        # 目录页
        toc_entries: list[Any] = []
        spine: list[Any] = ["nav"]

        total_words = 0
        for i, (num, ch_title, text) in enumerate(chapters, 1):
            chapter = epub.EpubHtml(
                title=ch_title,
                file_name=f"ch{i:03d}.xhtml",
                lang="zh-CN",
            )
            # 转换 markdown 到简易 HTML
            html_content = self._markdown_to_html(text, ch_title)
            chapter.set_content(html_content)
            book.add_item(chapter)
            spine.append(chapter)
            toc_entries.append(chapter)
            total_words += len(text.replace("\n", "").replace(" ", ""))

        book.toc = toc_entries
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        book.spine = spine

        epub.write_epub(str(output_file), book, {})
        return total_words

    @staticmethod
    def _markdown_to_html(text: str, title: str) -> str:
        """简易 markdown → HTML 转换"""
        lines = text.split("\n")
        html_lines: list[str] = [f"<h1>{title}</h1>"]
        for line in lines:
            line = line.strip()
            if not line:
                html_lines.append("<p></p>")
                continue
            if line.startswith("### "):
                html_lines.append(f"<h3>{line[4:]}</h3>")
            elif line.startswith("## "):
                html_lines.append(f"<h2>{line[3:]}</h2>")
            elif line.startswith("# "):
                html_lines.append(f"<h1>{line[2:]}</h1>")
            else:
                html_lines.append(f"<p>{line}</p>")
        return "\n".join(html_lines)


# ============================================================
# F11.1 导入
# ============================================================
@dataclass
class ImportResult:
    """导入结果"""

    success: bool
    source_file: Path
    detected_title: str = ""
    detected_chapters: int = 0
    world_file: Path | None = None
    character_files: list[Path] = field(default_factory=list)
    message: str = ""


@workflow("m11_import")
class ImportWorkflow:
    """导入工作流（F11.1）

    读取用户提供的 txt/markdown 草稿，用 LLM 反向解析构建设定集。

    用法：
        wf = ImportWorkflow(project_dir, llm=LLMClient())
        result = wf.import_draft(Path("my_draft.txt"))
    """

    IMPORT_SYSTEM_PROMPT = """你是小说设定提取专家。从用户提供的草稿文本中反向提取小说设定。

输出 JSON：
{
  "title": "小说标题（从文本推断）",
  "genre": "题材（如 xiuxian/romance/mystery，无法判断留空）",
  "synopsis": "故事简介，100-200字",
  "worldview": "世界观描述，200-400字",
  "power_system": "力量体系（如有）",
  "main_characters": [
    {"name": "姓名", "role": "protagonist|antagonist|supporting", "identity": "身份", "core_motivation": "动机"}
  ],
  "chapter_count": "检测到的章节数"
}

只输出 JSON，不要 ```json 标记。"""

    def __init__(
        self,
        project_dir: Path,
        llm: LLMClient | None = None,
        console: Console | None = None,
    ) -> None:
        self.project_dir = Path(project_dir)
        self.llm = llm or LLMClient()
        self.console = console or Console()
        self.sm = SettingManager(self.project_dir)

    def import_draft(
        self,
        source_file: Path,
        with_characters: bool = True,
    ) -> ImportResult:
        """导入草稿

        Args:
            source_file: 草稿文件路径（txt/markdown）
            with_characters: 是否同时生成角色档案（默认 True）

        Returns:
            ImportResult
        """
        source_file = Path(source_file)
        if not source_file.exists():
            return ImportResult(
                success=False,
                source_file=source_file,
                message=f"文件不存在: {source_file}",
            )

        text = source_file.read_text(encoding="utf-8")
        # 截断避免超长
        if len(text) > 10000:
            text = text[:10000]

        # 调用 LLM 提取设定
        resp = self.llm.chat_utility(
            messages=[
                {"role": "system", "content": self.IMPORT_SYSTEM_PROMPT},
                {"role": "user", "content": f"请从以下草稿提取设定：\n\n{text}"},
            ],
            temperature=0.2,
        )

        try:
            data = parse_llm_json(resp.text)
        except ValueError:
            return ImportResult(
                success=False,
                source_file=source_file,
                message="LLM 输出解析失败，无法提取设定",
            )

        title = str(data.get("title", source_file.stem))
        genre = str(data.get("genre", ""))
        synopsis = str(data.get("synopsis", ""))
        worldview = str(data.get("worldview", ""))
        power_system = str(data.get("power_system", ""))
        chapter_count = int(data.get("chapter_count", 0) or 0)
        characters = data.get("main_characters", []) or []

        # 构造 world.md
        metadata: dict[str, Any] = {
            "title": title,
            "genre": genre or "unknown",
            "scope": "medium",
            "tone": "neutral",
            "imported_from": str(source_file.name),
        }
        if power_system:
            metadata["power_system"] = power_system

        content_parts = ["# 总设定集", ""]
        if synopsis:
            content_parts.append(f"## 故事简介\n\n{synopsis}")
        if worldview:
            content_parts.append(f"## 世界观\n\n{worldview}")
        if power_system:
            content_parts.append(f"## 力量体系\n\n{power_system}")
        content_parts.append(f"\n> 本设定集由导入草稿 {source_file.name} 自动生成，请人工确认与补充。")

        content = "\n".join(content_parts)
        world_file = self.sm.save_world(metadata, content)

        # 生成角色档案
        char_files: list[Path] = []
        if with_characters and isinstance(characters, list):
            for ch in characters:
                if not isinstance(ch, dict):
                    continue
                name = str(ch.get("name", "")).strip()
                if not name:
                    continue
                cf = self._save_character_from_import(name, ch)
                if cf:
                    char_files.append(cf)

        msg_parts = [f"world.md（标题: {title}）"]
        if char_files:
            msg_parts.append(f"{len(char_files)} 个角色档案")
        msg_parts.append(f"检测到 {chapter_count} 章")

        return ImportResult(
            success=True,
            source_file=source_file,
            detected_title=title,
            detected_chapters=chapter_count,
            world_file=world_file,
            character_files=char_files,
            message=f"已从 {source_file.name} 导入设定，生成 " + "、".join(msg_parts),
        )

    def _save_character_from_import(
        self, name: str, data: dict[str, Any]
    ) -> Path | None:
        """根据导入数据生成角色档案"""
        role = str(data.get("role", "supporting"))
        identity = str(data.get("identity", ""))
        motivation = str(data.get("core_motivation", ""))

        metadata: dict[str, Any] = {
            "name": name,
            "role": role,
            "identity": identity,
            "imported": True,
        }

        content_lines = [f"# {name}", ""]
        if identity:
            content_lines.append(f"## 身份\n\n{identity}")
        if motivation:
            content_lines.append(f"## 核心动机\n\n{motivation}")
        content_lines.append("")
        content_lines.append(
            f"> 该角色档案由草稿导入自动生成，请人工确认与补充语言指纹、弧光等字段。"
        )

        try:
            return self.sm.save_character(name, metadata, "\n".join(content_lines))
        except Exception:
            return None


# ============================================================
# F11.3 完本附加产出
# ============================================================
@dataclass
class CompletionExtras:
    """完本附加产出"""

    afterword_file: Path | None = None
    character_anthology_file: Path | None = None
    world_summary_file: Path | None = None
    foreshadow_report_file: Path | None = None
    message: str = ""


@workflow("m11_completion_extras")
class CompletionExtrasWorkflow:
    """完本附加产出工作流（F11.3）

    生成：
        - 完本感言（LLM 生成）
        - 人物志（汇总 characters/*.md）
        - 世界观总结（复制 world.md）
        - 伏笔回收报告（复用 M13）
    """

    AFTERWORD_SYSTEM_PROMPT = """你是小说完本感言撰写助手。根据小说信息生成一段完本感言。

要求：
1. 真诚、有温度
2. 感谢读者陪伴
3. 简述创作心路
4. 200-400字
5. 直接输出正文，不要标题"""

    def __init__(
        self,
        project_dir: Path,
        llm: LLMClient | None = None,
        console: Console | None = None,
    ) -> None:
        self.project_dir = Path(project_dir)
        self.llm = llm or LLMClient()
        self.console = console or Console()
        self.sm = SettingManager(self.project_dir)

    def generate(
        self,
        output_dir: Path | None = None,
        skip_afterword: bool = False,
    ) -> CompletionExtras:
        """生成完本附加产出

        Args:
            output_dir: 输出目录，None 则用 project_dir/completion/
            skip_afterword: 跳过 LLM 生成感言（离线场景）

        Returns:
            CompletionExtras
        """
        if output_dir is None:
            output_dir = self.project_dir / "completion"
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        result = CompletionExtras()
        parts: list[str] = []

        # 1. 完本感言
        if not skip_afterword:
            afterword = self._generate_afterword()
            if afterword:
                afterword_file = output_dir / "afterword.md"
                afterword_file.write_text(afterword, encoding="utf-8")
                result.afterword_file = afterword_file
                parts.append("完本感言")

        # 2. 人物志
        char_file = self._compile_character_anthology(output_dir)
        if char_file:
            result.character_anthology_file = char_file
            parts.append("人物志")

        # 3. 世界观总结
        world_file = self._copy_world_summary(output_dir)
        if world_file:
            result.world_summary_file = world_file
            parts.append("世界观总结")

        # 4. 伏笔回收报告（复用 M13）
        foreshadow_file = self._generate_foreshadow_report(output_dir)
        if foreshadow_file:
            result.foreshadow_report_file = foreshadow_file
            parts.append("伏笔回收报告")

        result.message = f"已生成完本附加产出：{', '.join(parts)}"
        return result

    def _generate_afterword(self) -> str | None:
        """生成完本感言"""
        world_data = self.sm.load_world()
        if not world_data["exists"]:
            return None
        title = world_data["metadata"].get("title", "")
        synopsis = world_data["content"][:500]

        try:
            resp = self.llm.chat_utility(
                messages=[
                    {"role": "system", "content": self.AFTERWORD_SYSTEM_PROMPT},
                    {"role": "user", "content": f"小说标题：{title}\n简介摘要：{synopsis}"},
                ],
                temperature=0.7,
            )
            return f"# 完本感言\n\n{resp.text.strip()}"
        except Exception:
            return None

    def _compile_character_anthology(self, output_dir: Path) -> Path | None:
        """汇总角色档案"""
        chars = self.sm.list_characters()
        if not chars:
            return None

        lines: list[str] = ["# 人物志", ""]
        for name in chars:
            data = self.sm.load_character(name)
            if not data["exists"]:
                continue
            lines.append(f"## {name}")
            lines.append("")
            # 元数据
            for k, v in data["metadata"].items():
                lines.append(f"- **{k}**: {v}")
            lines.append("")
            lines.append(data["content"])
            lines.append("")
            lines.append("---")
            lines.append("")

        file = output_dir / "character_anthology.md"
        file.write_text("\n".join(lines), encoding="utf-8")
        return file

    def _copy_world_summary(self, output_dir: Path) -> Path | None:
        """复制世界观总结"""
        world_file = self.project_dir / "world.md"
        if not world_file.exists():
            return None
        dest = output_dir / "world_summary.md"
        dest.write_text(world_file.read_text(encoding="utf-8"), encoding="utf-8")
        return dest

    def _generate_foreshadow_report(self, output_dir: Path) -> Path | None:
        """生成伏笔回收报告（复用 M13）"""
        foreshadow_file = self.project_dir / "foreshadows.md"
        if not foreshadow_file.exists():
            return None
        try:
            from agent.workflows.m13_foreshadow import M13ForeshadowWorkflow

            wf = M13ForeshadowWorkflow(project_dir=self.project_dir, console=self.console)
            report = wf.generate_completion_report()
            if report.report_file and report.report_file.exists():
                # 复制到 completion 目录
                dest = output_dir / "foreshadow_report.md"
                dest.write_text(
                    report.report_file.read_text(encoding="utf-8"), encoding="utf-8"
                )
                return dest
        except Exception:
            pass
        return None
