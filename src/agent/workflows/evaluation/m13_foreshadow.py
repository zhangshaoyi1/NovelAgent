"""M13 伏笔管理工作流

基于 PRD F13.1-F13.4，实现伏笔全生命周期管理：

F13.1 伏笔登记表维护：
    - 解析 foreshadows.md 表格
    - 更新伏笔状态（未埋 → 已埋 → 已回收 / 已废弃）
    - 自动重算统计

F13.2 每章前检查（M5 已实现，此处复用）：
    - 检查本章是否应埋新伏笔
    - 检查是否有到期该回收的旧伏笔

F13.3 支线结束检查：
    - 检测当前支线是否有未回收伏笔
    - 输出预警清单（伏笔 ID + 内容 + 建议处理方式）

F13.4 完结伏笔回收报告：
    - 统计：总伏笔数 / 已回收 / 未回收 / 已废弃 / 回收率
    - 未回收伏笔清单（含应回收章节 + 关联角色）
    - 逾期未回收伏笔（预期回收点已过）
    - 输出 foreshadow_report.md
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from agent.core.engine.state_machine import Event, State, StateMachine
from agent.core.engine.workflow_registry import workflow


# ============================================================
# 数据类
# ============================================================

# P1-8：伏笔提前预警窗口（预期回收点前 N 章内标记为「即将到期」，对齐 MuMuAINovel remind_before_chapters）
REMIND_BEFORE_CHAPTERS = 3

# 紧急度三级（foreshadow_urgency 的返回值）
URGENCY_OVERDUE = "overdue"
URGENCY_DUE = "due"
URGENCY_NORMAL = "normal"


def foreshadow_urgency(
    state: str, expected_resolve: str, current_chapter: int,
    remind_before: int = REMIND_BEFORE_CHAPTERS,
) -> str:
    """**纯函数**：按预期回收点与当前章节计算伏笔紧急度。

    - ``overdue``：当前章已过预期回收点且未回收；
    - ``due``：距预期回收点不足 ``remind_before`` 章（含当章恰到期）且未回收；
    - ``normal``：其余情况（含未埋/已回收/已废弃/回收点无法解析章节号）。
    """
    if state in ("已回收", "已废弃") or current_chapter <= 0:
        return URGENCY_NORMAL
    m = re.search(r"ch(\d+)", expected_resolve or "")
    if not m:
        return URGENCY_NORMAL
    expected = int(m.group(1))
    if current_chapter > expected:
        return URGENCY_OVERDUE
    if current_chapter >= expected - remind_before:
        return URGENCY_DUE
    return URGENCY_NORMAL


@dataclass
class Foreshadow:
    """单条伏笔"""

    fid: str
    content: str
    planted_at: str  # 埋设位置，如 "S01/E01/ch003"
    expected_resolve: str  # 预期回收点
    state: str  # 未埋 / 已埋 / 已回收 / 已废弃
    related_characters: str

    @property
    def is_planted(self) -> bool:
        return self.state in ("已埋", "已回收")

    @property
    def is_resolved(self) -> bool:
        return self.state == "已回收"

    @property
    def is_overdue(self, current_chapter: int = 0) -> bool:
        """是否逾期（预期回收点已过但未回收）"""
        if self.state == "已回收" or self.state == "已废弃":
            return False
        # 从 expected_resolve 提取章节号
        m = re.search(r"ch(\d+)", self.expected_resolve)
        if m and current_chapter > 0:
            return current_chapter > int(m.group(1))
        return False

    def overdue_check(self, current_chapter: int) -> bool:
        return self.is_overdue if current_chapter == 0 else self._overdue_impl(current_chapter)

    def _overdue_impl(self, current_chapter: int) -> bool:
        if self.state in ("已回收", "已废弃"):
            return False
        m = re.search(r"ch(\d+)", self.expected_resolve)
        if m:
            return current_chapter > int(m.group(1))
        return False

    def urgency(self, current_chapter: int) -> str:
        """P1-8 紧急度三级：``overdue``（已逾期）/ ``due``（即将到期）/ ``normal``。

        仅"已埋未回收"的伏笔参与分级；预期回收点无法解析章节号时一律 normal。
        提前预警窗口 = ``REMIND_BEFORE_CHAPTERS``。
        """
        return foreshadow_urgency(self.state, self.expected_resolve, current_chapter)


@dataclass
class ForeshadowStats:
    """伏笔统计"""

    total: int = 0
    not_planted: int = 0
    planted: int = 0
    resolved: int = 0
    abandoned: int = 0
    overdue: int = 0
    due: int = 0  # P1-8：即将到期（预警窗口内、未逾期）

    @property
    def resolve_rate(self) -> float:
        """回收率"""
        if self.total == 0:
            return 0.0
        return self.resolved / self.total


@dataclass
class M13Report:
    """伏笔回收报告"""

    stats: ForeshadowStats
    unresolved: list[Foreshadow] = field(default_factory=list)
    overdue: list[Foreshadow] = field(default_factory=list)
    due: list[Foreshadow] = field(default_factory=list)  # P1-8：即将到期
    subline_unresolved: list[Foreshadow] = field(default_factory=list)
    report_file: Path | None = None


# ============================================================
# 伏笔管理工作流
# ============================================================
@workflow("m13_foreshadow")
class M13ForeshadowWorkflow:
    """M13 伏笔管理工作流"""

    def __init__(
        self,
        project_dir: Path,
        state_machine: StateMachine | None = None,
        console: Console | None = None,
    ) -> None:
        self.project_dir = Path(project_dir)
        self.state_machine = state_machine or StateMachine(self.project_dir)
        self.console = console or Console()
        self.foreshadow_file = self.project_dir / "foreshadows.md"

    # ============================================================
    # 解析
    # ============================================================
    def load_foreshadows(self) -> list[Foreshadow]:
        """解析 foreshadows.md 表格"""
        if not self.foreshadow_file.exists():
            return []
        text = self.foreshadow_file.read_text(encoding="utf-8")
        return self._parse_table(text)

    @staticmethod
    def _parse_table(text: str) -> list[Foreshadow]:
        """从 markdown 表格解析伏笔列表"""
        items: list[Foreshadow] = []
        # 匹配表格行：| F-XX | 内容 | 位置 | 回收点 | 状态 | 角色 |
        pattern = re.compile(
            r"^\|\s*(F-\d+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|"
        )
        for line in text.splitlines():
            m = pattern.match(line.strip())
            if m:
                fid, content, planted, expected, state, related = m.groups()
                # 跳过分隔行
                if fid == "F-XX" or "---" in content:
                    continue
                items.append(
                    Foreshadow(
                        fid=fid.strip(),
                        content=content.strip(),
                        planted_at=planted.strip(),
                        expected_resolve=expected.strip(),
                        state=state.strip(),
                        related_characters=related.strip(),
                    )
                )
        return items

    def compute_stats(
        self, items: list[Foreshadow] | None = None, current_chapter: int = 0
    ) -> ForeshadowStats:
        """计算统计"""
        if items is None:
            items = self.load_foreshadows()
        stats = ForeshadowStats(total=len(items))
        for f in items:
            if f.state == "未埋":
                stats.not_planted += 1
            elif f.state == "已埋":
                stats.planted += 1
            elif f.state == "已回收":
                stats.resolved += 1
            elif f.state == "已废弃":
                stats.abandoned += 1
            # 逾期检查
            if current_chapter > 0 and f._overdue_impl(current_chapter):
                stats.overdue += 1
            elif current_chapter > 0 and f.urgency(current_chapter) == URGENCY_DUE:
                stats.due += 1
        return stats

    # ============================================================
    # F13.2 每章前检查（复用 M5 逻辑，提供独立接口）
    # ============================================================
    def check_chapter_tasks(self, chapter_num: int) -> dict[str, list[str]]:
        """检查本章伏笔任务

        Returns:
            {"plant": [需埋设的], "resolve": [可回收的]}
        """
        items = self.load_foreshadows()
        plant_tasks: list[str] = []
        resolve_tasks: list[str] = []

        for f in items:
            # 检查本章是否应埋设
            planted_ch = self._extract_chapter_num(f.planted_at)
            if planted_ch == chapter_num and f.state == "未埋":
                plant_tasks.append(f"{f.fid}: {f.content}（预期回收：{f.expected_resolve}）")

            # 检查本章是否应回收
            resolve_ch = self._extract_chapter_num(f.expected_resolve)
            if resolve_ch == chapter_num and f.state == "已埋":
                resolve_tasks.append(f"{f.fid}: {f.content}")

            # 每 10 章强制回收 1 条
            if chapter_num % 10 == 0 and f.state == "已埋":
                resolve_tasks.append(f"{f.fid}: {f.content}（10 章强制回收）")

        return {"plant": plant_tasks, "resolve": resolve_tasks}

    @staticmethod
    def _extract_chapter_num(location: str) -> int:
        """从位置字符串提取章节号"""
        m = re.search(r"ch(\d+)", location)
        return int(m.group(1)) if m else 0

    # ============================================================
    # F13.3 支线结束检查
    # ============================================================
    def check_subline_end(self, subline_id: str) -> list[Foreshadow]:
        """支线结束时检查未回收伏笔

        Args:
            subline_id: 支线 ID，如 "S01_器灵人性觉醒"

        Returns:
            该支线中未回收的伏笔列表
        """
        items = self.load_foreshadows()
        # 提取支线前缀（S01）
        sub_prefix = re.match(r"(S\d+)", subline_id)
        if not sub_prefix:
            return []
        prefix = sub_prefix.group(1)

        unresolved: list[Foreshadow] = []
        for f in items:
            # 伏笔的埋设位置或回收点属于该支线
            if prefix in f.planted_at or prefix in f.expected_resolve:
                if f.state in ("未埋", "已埋"):
                    unresolved.append(f)
        return unresolved

    # ============================================================
    # F13.4 完结伏笔回收报告
    # ============================================================
    def generate_completion_report(self) -> M13Report:
        """生成完结伏笔回收报告

        写入 foreshadow_report.md
        """
        self.state_machine.load()
        progress = self.state_machine.progress or {}
        current_chapter = int(progress.get("total_written", 0) or 0)

        items = self.load_foreshadows()
        stats = self.compute_stats(items, current_chapter)

        # 未回收
        unresolved = [
            f for f in items if f.state in ("未埋", "已埋")
        ]
        # 逾期
        overdue = [
            f for f in items if f._overdue_impl(current_chapter)
        ]
        # P1-8：即将到期（预警窗口内、未逾期、未回收）
        due = [
            f for f in items
            if f.state == "已埋" and f.urgency(current_chapter) == URGENCY_DUE
        ]

        # 写入报告文件
        report_file = self.project_dir / "foreshadow_report.md"
        content = self._render_report(stats, unresolved, overdue, current_chapter, due=due)
        report_file.write_text(content, encoding="utf-8")

        return M13Report(
            stats=stats,
            unresolved=unresolved,
            overdue=overdue,
            due=due,
            subline_unresolved=[],
            report_file=report_file,
        )

    def _render_report(
        self,
        stats: ForeshadowStats,
        unresolved: list[Foreshadow],
        overdue: list[Foreshadow],
        current_chapter: int,
        due: list[Foreshadow] | None = None,
    ) -> str:
        """渲染报告 markdown"""
        lines: list[str] = []
        lines.append("# 伏笔回收报告")
        lines.append("")
        lines.append(f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"> 当前进度：第 {current_chapter} 章")
        lines.append("")

        # 统计
        lines.append("## 统计总览")
        lines.append("")
        lines.append("| 指标 | 值 |")
        lines.append("|---|---|")
        lines.append(f"| 总伏笔数 | {stats.total} |")
        lines.append(f"| 未埋 | {stats.not_planted} |")
        lines.append(f"| 已埋 | {stats.planted} |")
        lines.append(f"| 已回收 | {stats.resolved} |")
        lines.append(f"| 已废弃 | {stats.abandoned} |")
        lines.append(f"| 逾期未回收 | {stats.overdue} |")
        lines.append(f"| 即将到期（{REMIND_BEFORE_CHAPTERS} 章内） | {stats.due} |")
        rate = f"{stats.resolve_rate * 100:.1f}%" if stats.total > 0 else "N/A"
        lines.append(f"| 回收率 | {rate} |")
        lines.append("")

        # 未回收清单
        if unresolved:
            lines.append("## 未回收伏笔清单")
            lines.append("")
            lines.append("| ID | 内容 | 状态 | 埋设位置 | 预期回收点 | 关联角色 |")
            lines.append("|---|---|---|---|---|---|")
            for f in unresolved:
                lines.append(
                    f"| {f.fid} | {f.content} | {f.state} | "
                    f"{f.planted_at} | {f.expected_resolve} | {f.related_characters} |"
                )
            lines.append("")

        # 逾期清单
        if overdue:
            lines.append("## ⚠ 逾期未回收伏笔")
            lines.append("")
            lines.append("> 预期回收点已过但未回收，建议尽快处理。")
            lines.append("")
            lines.append("| ID | 内容 | 预期回收点 | 关联角色 |")
            lines.append("|---|---|---|---|")
            for f in overdue:
                lines.append(
                    f"| {f.fid} | {f.content} | {f.expected_resolve} | {f.related_characters} |"
                )
            lines.append("")

        # P1-8：即将到期清单
        if due:
            lines.append("## ⏳ 即将到期伏笔")
            lines.append("")
            lines.append(f"> 预期回收点在后续 {REMIND_BEFORE_CHAPTERS} 章内，写作时优先安排自然回收。")
            lines.append("")
            lines.append("| ID | 内容 | 预期回收点 | 关联角色 |")
            lines.append("|---|---|---|---|")
            for f in due:
                lines.append(
                    f"| {f.fid} | {f.content} | {f.expected_resolve} | {f.related_characters} |"
                )
            lines.append("")

        # 建议
        lines.append("## 处理建议")
        lines.append("")
        if stats.overdue > 0:
            lines.append(f"- **紧急**：{stats.overdue} 条伏笔已逾期，建议在后续 3-5 章内回收")
        if stats.due > 0:
            lines.append(f"- **预警**：{stats.due} 条伏笔将在 {REMIND_BEFORE_CHAPTERS} 章内到期，优先安排回收")
        if stats.planted > 0:
            lines.append(f"- {stats.planted} 条已埋伏笔待回收，按预期回收点安排")
        if stats.not_planted > 0:
            lines.append(f"- {stats.not_planted} 条伏笔尚未埋设，按计划在指定章节埋入")
        if stats.resolve_rate < 0.5 and stats.total > 0:
            lines.append("- 回收率低于 50%，建议检查伏笔设计是否过于分散")
        if not unresolved:
            lines.append("- ✅ 所有伏笔已回收或废弃，无遗留问题")
        lines.append("")

        return "\n".join(lines)

    # ============================================================
    # 更新伏笔状态
    # ============================================================
    def update_state(self, fid: str, new_state: str) -> bool:
        """更新指定伏笔的状态

        Args:
            fid: 伏笔 ID，如 "F-01"
            new_state: 新状态（未埋/已埋/已回收/已废弃）

        Returns:
            True 表示更新成功
        """
        valid_states = {"未埋", "已埋", "已回收", "已废弃"}
        if new_state not in valid_states:
            raise ValueError(f"非法状态：{new_state}，可选：{sorted(valid_states)}")

        if not self.foreshadow_file.exists():
            return False

        text = self.foreshadow_file.read_text(encoding="utf-8")
        lines = text.splitlines()
        updated = False

        for i, line in enumerate(lines):
            # 匹配 F-XX 行
            m = re.match(r"^(\|\s*F-\d+\s*\|.*?\|.*?\|.*?\|.*?\|)\s*(.+?)\s*(\|.*)$", line)
            if m and fid in line:
                # 替换状态列（第 5 列）
                parts = line.split("|")
                if len(parts) >= 7:
                    # parts[0]=空, [1]=ID, [2]=内容, [3]=埋设, [4]=回收, [5]=状态, [6]=角色, [7]=空
                    parts[5] = f" {new_state} "
                    lines[i] = "|".join(parts)
                    updated = True
                    break

        if updated:
            # 重算统计
            items = self._parse_table("\n".join(lines))
            stats = self.compute_stats(items)
            # 更新统计区块
            self.foreshadow_file.write_text(
                self._rebuild_with_stats("\n".join(lines), stats),
                encoding="utf-8",
            )
        return updated

    @staticmethod
    def _rebuild_with_stats(text: str, stats: ForeshadowStats) -> str:
        """重写统计区块"""
        rate = f"{stats.resolve_rate * 100:.1f}%" if stats.total > 0 else "N/A"
        stats_block = (
            f"## 统计\n\n"
            f"- 未埋：{stats.not_planted}\n"
            f"- 已埋：{stats.planted}\n"
            f"- 已回收：{stats.resolved}\n"
            f"- 已废弃：{stats.abandoned}\n"
            f"- 回收率：{rate}\n"
        )
        # 替换原有统计区块
        pattern = re.compile(r"## 统计\n.*?(?=\n## |\Z)", re.DOTALL)
        if pattern.search(text):
            return pattern.sub(stats_block.rstrip(), text)
        return text + "\n\n" + stats_block

    # ============================================================
    # 展示
    # ============================================================
    def show_dashboard(self) -> None:
        """展示伏笔仪表盘"""
        self.state_machine.load()
        progress = self.state_machine.progress or {}
        current_chapter = int(progress.get("total_written", 0) or 0)

        items = self.load_foreshadows()
        stats = self.compute_stats(items, current_chapter)

        table = Table(title="伏笔管理仪表盘")
        table.add_column("指标", style="cyan")
        table.add_column("值", style="white")
        rate = f"{stats.resolve_rate * 100:.1f}%" if stats.total > 0 else "N/A"
        table.add_row("总伏笔数", str(stats.total))
        table.add_row("未埋", str(stats.not_planted))
        table.add_row("已埋", str(stats.planted))
        table.add_row("已回收", str(stats.resolved))
        table.add_row("已废弃", str(stats.abandoned))
        table.add_row("逾期", str(stats.overdue))
        table.add_row("回收率", rate)
        self.console.print(table)

        if stats.overdue > 0:
            self.console.print(
                Panel(
                    f"[bold red]⚠ {stats.overdue} 条伏笔已逾期！[/bold red]\n"
                    "建议运行 /foreshadow-report 生成详细报告",
                    border_style="red",
                )
            )
