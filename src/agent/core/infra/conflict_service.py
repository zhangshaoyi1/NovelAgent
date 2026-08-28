"""冲突仲裁核心服务（F12.1）

从 M12 工作流上提至 core 层，供 M5（前置门禁）与 M12（设定冲突检测）共用，
消除工作流之间的直接耦合。

依赖仅限 core 层与 prompts/utils，不反向依赖任何 workflow。
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

from agent.core.story.setting_manager import SettingManager
from agent.prompts import (
    M12_CONFLICT_SYSTEM_PROMPT,
    M12_CONFLICT_USER_TEMPLATE,
    M12_CONTENT_AUDIT_SYSTEM_PROMPT,
    M12_CONTENT_AUDIT_USER_TEMPLATE,
    M12_SUMMARY_SYSTEM_PROMPT,
    M12_SUMMARY_USER_TEMPLATE,
)
from agent.utils import parse_llm_json


@dataclass
class Conflict:
    """单条设定冲突"""

    field: str
    existing: str
    new: str
    severity: str  # high | medium | low
    affected_chapters: list[int] = field(default_factory=list)
    suggestion: str = ""


@dataclass
class ConflictReport:
    """设定冲突报告"""

    conflicts: list[Conflict] = field(default_factory=list)
    summary: str = ""

    @property
    def has_conflict(self) -> bool:
        return len(self.conflicts) > 0

    @property
    def high_severity_count(self) -> int:
        return sum(1 for c in self.conflicts if c.severity == "high")

    @property
    def needs_arbitration(self) -> bool:
        """是否需要用户仲裁（存在 high 严重度冲突）"""
        return self.high_severity_count > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "conflicts": [
                {
                    "field": c.field,
                    "existing": c.existing,
                    "new": c.new,
                    "severity": c.severity,
                    "affected_chapters": c.affected_chapters,
                    "suggestion": c.suggestion,
                }
                for c in self.conflicts
            ],
            "summary": self.summary,
        }


class ConflictArbiter:
    """设定冲突仲裁器（F12.1）

    用法：
        arbiter = ConflictArbiter(project_dir, llm=LLMClient())
        report = arbiter.check_new_setting("主角境界提升到金丹期")
        if report.needs_arbitration:
            # 提示用户仲裁
            ...
    """

    def __init__(
        self,
        project_dir: Path,
        llm: Any | None = None,
        setting_manager: SettingManager | None = None,
        console: Console | None = None,
    ) -> None:
        self.project_dir = Path(project_dir)
        self.llm = llm or self._default_llm()
        self.sm = setting_manager or SettingManager(self.project_dir)
        self.console = console or Console()

    @staticmethod
    def _default_llm() -> Any:
        from agent.client import LLMClient
        return LLMClient()

    def check_new_setting(
        self,
        new_setting: str,
        subline_id: str | None = None,
    ) -> ConflictReport:
        """检测用户新设定与现有设定的冲突

        Args:
            new_setting: 用户提交的新设定文本
            subline_id: 指定支线 ID（None 则用当前支线）

        Returns:
            ConflictReport
        """
        # 加载现有设定
        world_data = self.sm.load_world()
        world_content = world_data["content"] if world_data["exists"] else "（无）"

        subline_content = "（无）"
        if subline_id is None:
            # 尝试取第一个支线
            sublines = self.sm.list_sublines()
            if sublines:
                subline_id = sublines[0]
        if subline_id:
            subline_data = self.sm.load_subline(subline_id)
            if subline_data["exists"]:
                subline_content = subline_data["content"]

        # 加载角色档案
        characters_content = self._load_characters_content()

        # 调用 LLM 检测冲突
        user_msg = M12_CONFLICT_USER_TEMPLATE.format(
            world_content=world_content[:2000],
            subline_content=subline_content[:1500],
            characters_content=characters_content[:2000],
            new_setting=new_setting,
        )

        try:
            resp = self.llm.chat_utility(
                messages=[
                    {"role": "system", "content": M12_CONFLICT_SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.1,
            )
            data = parse_llm_json(resp.text)
        except (ValueError, Exception):
            return ConflictReport(
                conflicts=[],
                summary="冲突检测失败（LLM 解析异常）",
            )

        return self._parse_report(data)

    def _load_characters_content(self) -> str:
        """汇总所有角色档案内容"""
        names = self.sm.list_characters()
        if not names:
            return "（无角色档案）"
        parts: list[str] = []
        for name in names:
            data = self.sm.load_character(name)
            if data["exists"]:
                parts.append(f"### {name}\n{data['content'][:500]}")
        return "\n\n".join(parts)

    def _parse_report(self, data: dict[str, Any]) -> ConflictReport:
        """解析 LLM 输出为 ConflictReport"""
        conflicts: list[Conflict] = []
        for item in data.get("conflicts", []) or []:
            conflicts.append(
                Conflict(
                    field=str(item.get("field", "")),
                    existing=str(item.get("existing", "")),
                    new=str(item.get("new", "")),
                    severity=str(item.get("severity", "low")),
                    affected_chapters=[
                        int(c) for c in (item.get("affected_chapters") or []) if c
                    ],
                    suggestion=str(item.get("suggestion", "")),
                )
            )
        return ConflictReport(
            conflicts=conflicts,
            summary=str(data.get("summary", "")),
        )

    def show_report(self, report: ConflictReport) -> None:
        """在控制台展示冲突报告"""
        if not report.has_conflict:
            self.console.print("[green]✓ 无设定冲突[/green]")
            return

        table = Table(title="设定冲突报告", show_lines=True)
        table.add_column("字段", style="cyan")
        table.add_column("严重度", style="bold")
        table.add_column("现有")
        table.add_column("新设定")
        table.add_column("影响章节")
        table.add_column("建议")

        for c in report.conflicts:
            sev_style = (
                "red" if c.severity == "high" else "yellow" if c.severity == "medium" else "dim"
            )
            affected = ", ".join(f"ch{n:03d}" for n in c.affected_chapters) or "—"
            table.add_row(
                c.field,
                f"[{sev_style}]{c.severity}[/{sev_style}]",
                c.existing[:60],
                c.new[:60],
                affected,
                c.suggestion[:60],
            )

        self.console.print(table)
        self.console.print(f"\n[dim]{report.summary}[/dim]")
        if report.needs_arbitration:
            self.console.print(
                f"\n[bold red]⚠ 检测到 {report.high_severity_count} 个高严重度冲突，需要用户仲裁[/bold red]"
            )
