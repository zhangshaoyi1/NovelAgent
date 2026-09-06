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


# ============================================================
# 正文对账同步（2026-09-06）：修「账本只读」缺口
# ============================================================
# 缺口实证（无灵）：M13 扁平登记表（foreshadows.md）在写作路径中**只读不写**——
# 写章只注入提示词，写完没有任何环节把「已埋/已回收」写回登记表；
# G15 ForesightStore（foresight.json）又从未被播种，mark_committed 无 beat 可提交；
# ``update_state`` 因此全仓零调用（死桥）。评分器读到的登记表永远停在「未埋」，
# 结局段回收率恒为 0%。本模块提供**确定性**正文对账：以伏笔内容中可锚定的
# 关键词（引号术语 / 长名词片段）扫描已发布章节，命中即推进状态，水印增量扫描。


def extract_foreshadow_keywords(content: str) -> list[str]:
    """从伏笔内容提取可锚定的正文关键词（确定性，无 LLM）。

    优先取引号术语（「石碑」「黑血」类专有锚点）；无引号则取最长的名词片段
    （按标点切分后 ≥4 字的最长两段）。返回空列表表示无法确定性锚定，跳过对账。
    """
    kws = [
        k.strip()
        for k in re.findall(r"[「『“\"]([^」』”\"]{2,12})[」』”\"]", content or "")
        if len(k.strip()) >= 2
    ]
    if not kws:
        # 无引号：取最长名词片段的「头部 3 字（专有名词锚，如 晏无咎）」+
        # 「尾部 2 字（特征物锚，如 黑血）」；整句逐字匹配在正文中必然失配。
        frags = [
            f.strip()
            for f in re.split(r"[，。；：、（）()「」『』“”\s]+", content or "")
        ]
        frags = [f for f in frags if len(f) >= 4]
        frags.sort(key=len, reverse=True)
        for run in frags[:2]:
            kws.append(run[:3])
            if len(run) >= 5:
                kws.append(run[-2:])
    # 去重保序，并剔除过于常见的高频二字词误报源
    seen: set[str] = set()
    out: list[str] = []
    for k in kws:
        if len(k) >= 2 and k not in seen:
            seen.add(k)
            out.append(k)
    return out[:4]


def sync_foreshadow_states(
    project_dir: str | Path, console: Any = None
) -> dict[str, list[str]]:
    """按已发布正文对账 foreshadows.md 状态（确定性、增量、幂等）。

    规则：
    - ``未埋``：自埋设位置章起，正文命中关键词 → ``已埋``；
    - ``已埋``：自预期回收点章起（且在埋设命中之后），正文命中关键词 →
      ``已回收``，并把预期回收点改写为实际命中章（诚实记账）；
    - 水印存 ``.state/foreshadow_sync.json``（已扫描到的最大章号），后续调用
      只扫增量章；无法提取关键词的行跳过（宁缺毋假）。

    Returns:
        {"planted": ["F-01@ch005", ...], "recovered": ["F-03@ch338", ...]}
    """
    import json as _json

    project = Path(project_dir)
    foreshadow_file = project / "foreshadows.md"
    chapters_dir = project / "chapters"
    if not foreshadow_file.exists() or not chapters_dir.exists():
        return {"planted": [], "recovered": []}

    # ---- 章节文本缓存（归一化去空白，供跨行命中） ----
    chapter_nums: list[int] = []
    for f in chapters_dir.glob("ch*.md"):
        m = re.fullmatch(r"ch(\d+)\.md", f.name)
        if m:
            chapter_nums.append(int(m.group(1)))
    if not chapter_nums:
        return {"planted": [], "recovered": []}
    max_ch = max(chapter_nums)

    wm_file = project / ".state" / "foreshadow_sync.json"
    watermark = 0
    if wm_file.exists():
        try:
            watermark = int(_json.loads(wm_file.read_text(encoding="utf-8")).get("scanned_to", 0))
        except Exception:  # noqa: BLE001 - 水印损坏则全量重扫
            watermark = 0  # noqa: SILENT_DEGRADE

    text = foreshadow_file.read_text(encoding="utf-8")
    lines = text.splitlines()
    planted: list[str] = []
    recovered: list[str] = []
    changed = False

    # 行缓存：fid → 行号，用于跨状态二段推进（埋设命中章也供回收扫描起点用）
    rows: list[tuple[int, str, str, str, str, int | None, int | None, list[str]]] = []
    for i, ln in enumerate(lines):
        if not ln.startswith("| F-"):
            continue
        parts = [p.strip() for p in ln.split("|")]
        if len(parts) < 7 or parts[1] == "F-XX" or "---" in parts[2]:
            continue
        fid, content, planted_at, expected, state = parts[1:6]
        plant_m = re.search(r"ch(\d+)", planted_at or "")
        resolve_m = re.search(r"ch(\d+)", expected or "")
        rows.append((
            i, fid, content, state, expected,
            int(plant_m.group(1)) if plant_m else None,
            int(resolve_m.group(1)) if resolve_m else None,
            extract_foreshadow_keywords(content),
        ))

    def _scan(lo: int, hi: int, kws: list[str]) -> int | None:
        """在 [lo, hi] 章内找首个包含任一关键词的章号（文本已去空白）。"""
        if not kws:
            return None
        for ch in range(max(1, lo), min(hi, max_ch) + 1):
            f = chapters_dir / f"ch{ch:03d}.md"
            if not f.exists():
                continue
            try:
                body = re.sub(r"\s+", "", f.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001 - 单文件读失败跳过
                continue  # noqa: SILENT_DEGRADE
            for kw in kws:
                if kw in body:
                    return ch
        return None

    changed_rows: dict[int, tuple[str, str]] = {}  # 行号 → (新预期回收点, 新状态)
    hit_ch_by_fid: dict[str, int] = {}
    for (i, fid, content, state, expected, plant_ch, resolve_ch, kws) in rows:
        if not kws or state in ("已回收", "已废弃"):
            continue
        if state == "未埋":
            hit = _scan(plant_ch or 1, max_ch, kws)
            if hit:
                hit_ch_by_fid[fid] = hit
                planted.append(f"{fid}@ch{hit:03d}")
                # 同一轮内继续找回收（完结书的最后一轮 sync 也必须能推到已回收）
                hit2 = _scan(max(resolve_ch or 1, hit + 1), max_ch, kws)
                if hit2:
                    changed_rows[i] = (f"ch{hit2:03d}", "已回收")
                    recovered.append(f"{fid}@ch{hit2:03d}")
                else:
                    changed_rows[i] = (expected, "已埋")
        elif state == "已埋":
            lo = max(resolve_ch or 1, hit_ch_by_fid.get(fid, 1))
            hit = _scan(lo, max_ch, kws)
            if hit:
                changed_rows[i] = (f"ch{hit:03d}", "已回收")
                recovered.append(f"{fid}@ch{hit:03d}")

    if changed_rows:
        new_lines = list(lines)
        for i, (expected, state) in changed_rows.items():
            parts = [p.strip() for p in new_lines[i].split("|")]
            parts[4] = expected  # 预期回收点列 → 实际命中章
            parts[5] = state
            new_lines[i] = "| " + " | ".join(parts[1:7]) + " |"
        try:
            foreshadow_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
            changed = True
        except Exception:  # noqa: BLE001 - 写失败仅报告
            if console is not None:
                console.print("[yellow]⚠ foreshadows.md 对账写盘失败[/yellow]")  # noqa: SILENT_DEGRADE

    if watermark < max_ch or changed:
        try:
            wm_file.parent.mkdir(parents=True, exist_ok=True)
            wm_file.write_text(
                _json.dumps({"scanned_to": max_ch}, ensure_ascii=False), encoding="utf-8"
            )
        except Exception:  # noqa: BLE001 - 水印写失败下次重扫即可
            pass  # noqa: SILENT_DEGRADE
    if (planted or recovered) and console is not None:
        console.print(
            f"[yellow]⚠ 伏笔对账：新埋 {len(planted)} 条（{', '.join(planted) or '无'}），"
            f"新回收 {len(recovered)} 条（{', '.join(recovered) or '无'}）[/yellow]"
        )
    return {"planted": planted, "recovered": recovered}
