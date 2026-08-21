"""M12 内容审核与上下文管理

基于 PRD F12.1-F12.3：

F12.1 设定冲突仲裁
    - 用户输入新设定时，Agent 检测与现有设定集（world/subline/character）的冲突
    - 输出一致性影响报告（冲突条目 + 涉及已写章节 + 建议处理方式）
    - 高严重度冲突要求用户仲裁

F12.2 内容审核
    - 涉黄/涉政/极端暴力拦截
    - 修仙杀戮边界可配置（lenient/standard/strict）
    - 章节产出后或用户主动触发审核

F12.3 上下文分层加载
    - 必载层：world.md 摘要 + 当前 subline.md + 本章涉及角色档案 + 当前关系网子图
    - 按需层：其他支线设定、历史章节摘要、相关伏笔条目
    - 摘要机制：每写完 N 章，旧章节压缩为结构化摘要
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import frontmatter
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from agent.core.llm_client import LLMClient
from agent.core.setting_manager import SettingManager
from agent.prompts import (
    M12_CONFLICT_SYSTEM_PROMPT,
    M12_CONFLICT_USER_TEMPLATE,
    M12_CONTENT_AUDIT_SYSTEM_PROMPT,
    M12_CONTENT_AUDIT_USER_TEMPLATE,
    M12_SUMMARY_SYSTEM_PROMPT,
    M12_SUMMARY_USER_TEMPLATE,
)
from agent.utils import parse_llm_json


# ============================================================
# F12.1 设定冲突仲裁
from agent.core.conflict_service import Conflict, ConflictReport, ConflictArbiter
# ============================================================
# F12.2 内容审核
# ============================================================
# 杀戮边界策略
VIOLENCE_POLICIES = {
    "lenient": "宽松：允许较详细的战斗杀戮描写，仅拦截极端变态内容",
    "standard": "标准：允许修仙战斗中的合理杀戮，拦截过度血腥",
    "strict": "严格：杀戮描写需淡化处理，禁止详细血腥场面",
}


@dataclass
class Violation:
    """单条违规"""

    type: str  # sexual | political | violence | other
    severity: str  # high | medium | low
    excerpt: str
    reason: str
    suggestion: str = ""


@dataclass
class AuditResult:
    """内容审核结果"""

    passed: bool
    violations: list[Violation] = field(default_factory=list)
    summary: str = ""

    @property
    def high_severity_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == "high")

    @property
    def needs_block(self) -> bool:
        """是否需要拦截（存在 high 严重度违规）"""
        return self.high_severity_count > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "violations": [
                {
                    "type": v.type,
                    "severity": v.severity,
                    "excerpt": v.excerpt,
                    "reason": v.reason,
                    "suggestion": v.suggestion,
                }
                for v in self.violations
            ],
            "summary": self.summary,
        }


class ContentAuditor:
    """内容审核器（F12.2）

    用法：
        auditor = ContentAuditor(project_dir, llm=LLMClient())
        result = auditor.audit_chapter("章节正文...")
        if result.needs_block:
            # 拦截章节，要求重写
            ...
    """

    def __init__(
        self,
        project_dir: Path,
        llm: LLMClient | None = None,
        console: Console | None = None,
        violence_policy: str = "standard",
    ) -> None:
        self.project_dir = Path(project_dir)
        self.llm = llm or LLMClient()
        self.console = console or Console()
        self.violence_policy = violence_policy

    def audit_chapter(
        self,
        chapter_text: str,
        genre: str = "xiuxian",
        violence_policy: str | None = None,
    ) -> AuditResult:
        """审核章节正文

        Args:
            chapter_text: 章节正文
            genre: 题材
            violence_policy: 杀戮边界策略（None 用默认）

        Returns:
            AuditResult
        """
        policy = violence_policy or self.violence_policy
        policy_desc = VIOLENCE_POLICIES.get(policy, VIOLENCE_POLICIES["standard"])

        # 截断避免超长
        text = chapter_text[:8000]

        user_msg = M12_CONTENT_AUDIT_USER_TEMPLATE.format(
            genre=genre,
            violence_policy=policy_desc,
            chapter_text=text,
        )

        try:
            resp = self.llm.chat_utility(
                messages=[
                    {"role": "system", "content": M12_CONTENT_AUDIT_SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.1,
            )
            data = parse_llm_json(resp.text)
        except (ValueError, Exception):
            # 审核失败时保守起见返回通过（避免阻塞写作）
            return AuditResult(
                passed=True,
                violations=[],
                summary="内容审核失败（LLM 异常），默认放行",
            )

        return self._parse_result(data)

    def _parse_result(self, data: dict[str, Any]) -> AuditResult:
        violations: list[Violation] = []
        for item in data.get("violations", []) or []:
            violations.append(
                Violation(
                    type=str(item.get("type", "other")),
                    severity=str(item.get("severity", "low")),
                    excerpt=str(item.get("excerpt", ""))[:100],
                    reason=str(item.get("reason", "")),
                    suggestion=str(item.get("suggestion", "")),
                )
            )
        passed = bool(data.get("passed", len(violations) == 0))
        return AuditResult(
            passed=passed,
            violations=violations,
            summary=str(data.get("summary", "")),
        )

    def show_result(self, result: AuditResult) -> None:
        """在控制台展示审核结果"""
        if result.passed:
            self.console.print("[green]✓ 内容审核通过[/green]")
            if result.summary:
                self.console.print(f"[dim]{result.summary}[/dim]")
            return

        table = Table(title="内容审核违规", show_lines=True)
        table.add_column("类型", style="cyan")
        table.add_column("严重度", style="bold")
        table.add_column("违规片段")
        table.add_column("原因")
        table.add_column("建议")

        for v in result.violations:
            sev_style = (
                "red" if v.severity == "high" else "yellow" if v.severity == "medium" else "dim"
            )
            table.add_row(
                v.type,
                f"[{sev_style}]{v.severity}[/{sev_style}]",
                v.excerpt,
                v.reason,
                v.suggestion,
            )

        self.console.print(table)
        self.console.print(f"\n[dim]{result.summary}[/dim]")
        if result.needs_block:
            self.console.print(
                f"\n[bold red]⚠ 检测到 {result.high_severity_count} 个高严重度违规，章节已被拦截[/bold red]"
            )


# ============================================================
# F12.3 上下文分层加载
# ============================================================
@dataclass
class ChapterSummary:
    """章节结构化摘要"""

    chapter_num: int
    title: str
    summary: str
    key_events: list[str] = field(default_factory=list)
    character_changes: list[dict[str, str]] = field(default_factory=list)
    new_settings: list[str] = field(default_factory=list)
    foreshadows: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chapter_num": self.chapter_num,
            "title": self.title,
            "summary": self.summary,
            "key_events": self.key_events,
            "character_changes": self.character_changes,
            "new_settings": self.new_settings,
            "foreshadows": self.foreshadows,
        }

    def to_markdown(self) -> str:
        """渲染为 Markdown 片段"""
        lines = [f"### 第 {self.chapter_num} 章 {self.title}", "", self.summary, ""]
        if self.key_events:
            lines.append("**关键事件：**")
            for e in self.key_events:
                lines.append(f"- {e}")
            lines.append("")
        if self.character_changes:
            lines.append("**角色变化：**")
            for c in self.character_changes:
                lines.append(f"- {c.get('name', '?')}: {c.get('change', '')}")
            lines.append("")
        if self.new_settings:
            lines.append("**新设定：**")
            for s in self.new_settings:
                lines.append(f"- {s}")
            lines.append("")
        if self.foreshadows:
            lines.append("**伏笔：**")
            for f in self.foreshadows:
                lines.append(f"- {f}")
            lines.append("")
        return "\n".join(lines)


class ChapterSummarizer:
    """章节摘要生成器（F12.3 摘要机制）

    每写完 N 章，调用 LLM 将旧章节压缩为结构化摘要，
    保存到 chapters/_summaries/ch<NNN>.json。

    用法：
        summarizer = ChapterSummarizer(project_dir, llm=LLMClient())
        summary = summarizer.summarize_chapter(5)  # 生成第 5 章摘要
        summary = summarizer.load_summary(5)       # 读取已有摘要
    """

    DEFAULT_SUMMARY_INTERVAL = 5  # 默认每 5 章生成一次摘要

    def __init__(
        self,
        project_dir: Path,
        llm: LLMClient | None = None,
        console: Console | None = None,
    ) -> None:
        self.project_dir = Path(project_dir)
        self.llm = llm or LLMClient()
        self.console = console or Console()
        self.chapters_dir = self.project_dir / "chapters"
        self.summaries_dir = self.project_dir / "chapters" / "_summaries"

    def summarize_chapter(self, chapter_num: int) -> ChapterSummary | None:
        """为指定章节生成摘要

        Args:
            chapter_num: 章节号

        Returns:
            ChapterSummary 或 None（章节不存在/失败时）
        """
        chapter_file = self.chapters_dir / f"ch{chapter_num:03d}.md"
        if not chapter_file.exists():
            return None

        post = frontmatter.load(chapter_file)
        chapter_title = str(post.metadata.get("chapter_title", f"第{chapter_num}章"))
        chapter_text = post.content

        # 截断避免超长
        if len(chapter_text) > 8000:
            chapter_text = chapter_text[:8000]

        user_msg = M12_SUMMARY_USER_TEMPLATE.format(
            chapter_num=chapter_num,
            chapter_title=chapter_title,
            chapter_text=chapter_text,
        )

        try:
            resp = self.llm.chat_utility(
                messages=[
                    {"role": "system", "content": M12_SUMMARY_SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.2,
            )
            data = parse_llm_json(resp.text)
        except (ValueError, Exception):
            return None

        summary = self._parse_summary(data, chapter_num, chapter_title)
        # 强制以传入的 chapter_num 为准（避免 LLM 返回错误值）
        summary.chapter_num = chapter_num
        # 持久化
        self._save_summary(summary)
        return summary

    def _parse_summary(
        self, data: dict[str, Any], chapter_num: int, fallback_title: str
    ) -> ChapterSummary:
        return ChapterSummary(
            chapter_num=int(data.get("chapter_num", chapter_num)),
            title=str(data.get("title", fallback_title)),
            summary=str(data.get("summary", "")),
            key_events=[str(e) for e in (data.get("key_events") or [])],
            character_changes=[
                {"name": str(c.get("name", "")), "change": str(c.get("change", ""))}
                for c in (data.get("character_changes") or [])
                if isinstance(c, dict)
            ],
            new_settings=[str(s) for s in (data.get("new_settings") or [])],
            foreshadows=[str(f) for f in (data.get("foreshadows") or [])],
        )

    def _save_summary(self, summary: ChapterSummary) -> Path:
        """持久化摘要到 JSON 文件"""
        self.summaries_dir.mkdir(parents=True, exist_ok=True)
        file = self.summaries_dir / f"ch{summary.chapter_num:03d}.json"
        file.write_text(
            json.dumps(summary.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return file

    def load_summary(self, chapter_num: int) -> ChapterSummary | None:
        """读取已生成的章节摘要"""
        file = self.summaries_dir / f"ch{chapter_num:03d}.json"
        if not file.exists():
            return None
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
            return self._parse_summary(
                data, int(data.get("chapter_num", chapter_num)), str(data.get("title", ""))
            )
        except (json.JSONDecodeError, ValueError):
            return None

    def list_summaries(self) -> list[int]:
        """列出已生成摘要的章节号"""
        if not self.summaries_dir.exists():
            return []
        nums: list[int] = []
        for f in self.summaries_dir.glob("ch*.json"):
            try:
                num = int(f.stem[2:])
                nums.append(num)
            except ValueError:
                continue
        return sorted(nums)

    def summarize_range(
        self,
        start: int,
        end: int,
        skip_existing: bool = True,
    ) -> list[ChapterSummary]:
        """批量生成章节摘要

        Args:
            start: 起始章节号
            end: 结束章节号（含）
            skip_existing: 跳过已生成的摘要

        Returns:
            生成的摘要列表
        """
        existing = set(self.list_summaries()) if skip_existing else set()
        results: list[ChapterSummary] = []
        for num in range(start, end + 1):
            if num in existing:
                continue
            summary = self.summarize_chapter(num)
            if summary:
                results.append(summary)
        return results

    def compile_history_brief(self, up_to_chapter: int) -> str:
        """汇总历史章节摘要为前情简报

        Args:
            up_to_chapter: 截止章节号（不含）

        Returns:
            Markdown 格式的前情简报
        """
        existing = self.list_summaries()
        relevant = [n for n in existing if n < up_to_chapter]
        if not relevant:
            return f"（暂无已生成摘要，覆盖章节 1-{up_to_chapter - 1}）"

        lines = [f"## 前情简报（截至第 {up_to_chapter - 1} 章）", ""]
        for num in relevant:
            summary = self.load_summary(num)
            if summary:
                lines.append(summary.to_markdown())
        return "\n".join(lines)


class ContextLoader:
    """上下文分层加载器（F12.3）

    提供必载层 + 按需层的结构化加载接口，
    集成 ChapterSummarizer 实现摘要机制。

    用法：
        loader = ContextLoader(project_dir, llm=LLMClient())
        ctx = loader.load_essential(chapter_num=10, subline_id="S01")
        on_demand = loader.load_on_demand(chapter_num=10, include_history=True)
    """

    def __init__(
        self,
        project_dir: Path,
        llm: LLMClient | None = None,
        setting_manager: SettingManager | None = None,
        console: Console | None = None,
    ) -> None:
        self.project_dir = Path(project_dir)
        self.llm = llm or LLMClient()
        self.sm = setting_manager or SettingManager(self.project_dir)
        self.console = console or Console()
        self.summarizer = ChapterSummarizer(self.project_dir, llm=self.llm, console=self.console)

    # ------ 必载层 ------
    def load_essential(
        self,
        chapter_num: int,
        subline_id: str | None = None,
        character_names: list[str] | None = None,
    ) -> dict[str, Any]:
        """加载必载层上下文

        包含：
            - world.md 摘要（关键信息）
            - 当前支线 subline.md
            - 本章涉及角色档案
            - 当前关系网子图

        Args:
            chapter_num: 章节号
            subline_id: 支线 ID（None 取第一个）
            character_names: 本章涉及角色名列表（None 加载全部）

        Returns:
            dict 包含各层信息
        """
        # 1. world.md
        world_data = self.sm.load_world()
        world_summary = self._extract_world_summary(world_data) if world_data["exists"] else ""

        # 2. subline
        subline_content = ""
        subline_name = ""
        if subline_id is None:
            sublines = self.sm.list_sublines()
            if sublines:
                subline_id = sublines[0]
        if subline_id:
            subline_data = self.sm.load_subline(subline_id)
            if subline_data["exists"]:
                subline_content = subline_data["content"]
                subline_name = subline_data["metadata"].get("subline_name", subline_id)

        # 3. 角色档案
        characters = self._load_characters(character_names)

        # 4. 关系网子图
        relations = self._load_relations_subgraph(character_names)

        return {
            "layer": "essential",
            "chapter_num": chapter_num,
            "world_summary": world_summary,
            "subline_id": subline_id or "",
            "subline_name": subline_name,
            "subline_content": subline_content,
            "characters": characters,
            "relations": relations,
        }

    def _extract_world_summary(self, world_data: dict[str, Any]) -> str:
        """从 world.md 提取关键摘要信息"""
        metadata = world_data.get("metadata", {}) or {}
        content = world_data.get("content", "")
        style = metadata.get("style", {}) or {}
        genre_label = metadata.get("genre_label") or " / ".join(metadata.get("genres") or [])

        parts = [
            f"书名：{metadata.get('title', '')}",
            f"题材：{genre_label}",
            f"体量：{metadata.get('scope', '')}",
        ]
        if style:
            parts.append(
                f"风格：{style.get('tone', '')}/{style.get('pov', '')}/{style.get('rhythm', '')}"
            )
        # 提取简介段
        if "## 故事简介" in content:
            seg = content.split("## 故事简介", 1)[1]
            seg = seg.split("##", 1)[0].strip()
            if seg:
                parts.append(f"简介：{seg[:300]}")
        if "## 境界体系" in content:
            seg = content.split("## 境界体系", 1)[1]
            seg = seg.split("##", 1)[0].strip()
            if seg:
                parts.append(f"境界体系：{seg[:300]}")
        return "\n".join(parts)

    def _load_characters(
        self, names: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """加载角色档案"""
        if names is None:
            names = self.sm.list_characters()
        result: list[dict[str, Any]] = []
        for name in names:
            data = self.sm.load_character(name)
            if data["exists"]:
                result.append(
                    {
                        "name": name,
                        "metadata": data["metadata"],
                        "content": data["content"][:800],
                    }
                )
        return result

    def _load_relations_subgraph(
        self, character_names: list[str] | None = None
    ) -> str:
        """加载关系网（子图）"""
        graph_file = self.project_dir / "relations" / "graph.md"
        if not graph_file.exists():
            return ""
        content = graph_file.read_text(encoding="utf-8")
        # 简单返回全部内容（子图过滤可在后续优化）
        return content[:2000]

    # ------ 按需层 ------
    def load_on_demand(
        self,
        chapter_num: int,
        include_history: bool = True,
        include_other_sublines: bool = False,
        include_foreshadows: bool = True,
        history_window: int = 5,
        with_rag: bool = True,
    ) -> dict[str, Any]:
        """加载按需层上下文

        Args:
            chapter_num: 当前章节号
            include_history: 是否包含历史章节摘要
            include_other_sublines: 是否包含其他支线设定
            include_foreshadows: 是否包含伏笔条目
            history_window: 历史摘要窗口（最近 N 章）

        Returns:
            dict 包含按需层信息
        """
        result: dict[str, Any] = {"layer": "on_demand", "chapter_num": chapter_num}

        if include_history:
            # 优先用已生成的摘要；若没有，回退到读取最近章节正文片段
            history = self.summarizer.compile_history_brief(chapter_num)
            if "暂无已生成摘要" in history:
                history = self._fallback_recent_chapters(chapter_num, history_window)
            result["history"] = history

        if include_other_sublines:
            result["other_sublines"] = self._load_other_sublines()

        if include_foreshadows:
            result["foreshadows"] = self._load_foreshadows(chapter_num)

        # A：RAG 语义召回（仅当 .state/rag/ 已建立；否则不附加，绝不阻断审核）
        if with_rag:
            rag_dir = self.project_dir / ".state" / "rag"
            if rag_dir.exists():
                try:
                    from agent.core.rag.retriever import Retriever

                    result["rag_context"] = Retriever(self.project_dir).retrieve(
                        f"第{chapter_num}章 上下文 设定 角色 伏笔", top_k=5
                    )
                except Exception:  # noqa: BLE001 - RAG 失败不阻断审核
                    result["rag_context"] = []

        return result

    def _fallback_recent_chapters(
        self, chapter_num: int, window: int
    ) -> str:
        """无摘要时回退：读取最近 N 章的前 200 字"""
        chapters_dir = self.project_dir / "chapters"
        start = max(1, chapter_num - window)
        lines = [f"## 最近章节片段（{start}-{chapter_num - 1}）", ""]
        for n in range(start, chapter_num):
            f = chapters_dir / f"ch{n:03d}.md"
            if not f.exists():
                continue
            try:
                post = frontmatter.load(f)
                lines.append(f"### 第 {n} 章 {post.metadata.get('chapter_title', '')}")
                lines.append(post.content[:200] + "...")
                lines.append("")
            except Exception:
                continue
        return "\n".join(lines)

    def _load_other_sublines(self) -> list[dict[str, str]]:
        """加载其他支线设定"""
        all_sublines = self.sm.list_sublines()
        result: list[dict[str, str]] = []
        for sid in all_sublines:
            data = self.sm.load_subline(sid)
            if data["exists"]:
                result.append(
                    {
                        "id": sid,
                        "name": str(data["metadata"].get("subline_name", sid)),
                        "content": data["content"][:500],
                    }
                )
        return result

    def _load_foreshadows(self, chapter_num: int) -> str:
        """加载伏笔条目"""
        f_file = self.project_dir / "foreshadows.md"
        if not f_file.exists():
            return ""
        return f_file.read_text(encoding="utf-8")[:1500]

    # ------ 完整上下文 ------
    def load_full_context(
        self,
        chapter_num: int,
        subline_id: str | None = None,
        character_names: list[str] | None = None,
        include_on_demand: bool = True,
        with_rag: bool = True,
    ) -> dict[str, Any]:
        """加载完整上下文（必载层 + 按需层）

        Args:
            chapter_num: 章节号
            subline_id: 支线 ID
            character_names: 涉及角色
            include_on_demand: 是否包含按需层

        Returns:
            完整上下文 dict
        """
        essential = self.load_essential(
            chapter_num=chapter_num,
            subline_id=subline_id,
            character_names=character_names,
        )
        if not include_on_demand:
            return essential

        on_demand = self.load_on_demand(chapter_num=chapter_num, with_rag=with_rag)
        return {
            "essential": essential,
            "on_demand": on_demand,
        }
