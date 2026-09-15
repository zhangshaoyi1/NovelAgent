"""M10 失败回退与续作恢复工作流

基于 PRD F10.1-F10.3：

F10.1 分叉点回滚
    - 用户指定"回滚到第 N 章的分叉点"
    - Agent 基于该点重建状态（progress.total_written = N-1）
    - 后续章节（>N）标记为废弃，移动到 chapters/_archived/（不删除，可参考）
    - 同时归档受影响的进度指针

F10.2 续作恢复
    - 用户长时间未操作后回来
    - Agent 主动输出续作简报：
      * 上次写到哪（章节/支线/时间）
      * 3 条悬而未决的剧情线（当前路线节点 + 未完成支线）
      * 未回收伏笔清单（按优先级）
      * 关系网最近变化（archived 边）
      * 建议下一步
    - 仅在 PAUSED 状态可用（F16.2 命令门禁）

F10.3 状态机持久化
    - 已在 StateMachine 实现（.state/state.json）
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime

from agent.core.progress import next_chapter
from agent.core.infra.degrade import degrade
from pathlib import Path
from typing import Any
from agent.core.engine.workflow_registry import workflow

import frontmatter
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from agent.core.engine.state_machine import State, StateMachine


# ============================================================
# F10.1 分叉点回滚
# ============================================================
@dataclass
class RollbackResult:
    """回滚结果"""

    success: bool
    target_chapter: int  # 回滚到第 N 章（继续从 N 写起）
    archived_chapters: list[str] = field(default_factory=list)  # 被归档的章节文件名
    old_progress: dict[str, Any] = field(default_factory=dict)
    new_progress: dict[str, Any] = field(default_factory=dict)
    message: str = ""


@workflow("m10_rollback")
class M10RollbackWorkflow:
    """分叉点回滚工作流（F10.1）

    用法：
        wf = M10RollbackWorkflow(project_dir)
        result = wf.rollback_to_chapter(20)  # 回滚到第 20 章（保留 1-19，归档 20+）
    """

    def __init__(
        self,
        project_dir: Path,
        state_machine: StateMachine | None = None,
        console: Console | None = None,
    ) -> None:
        self.project_dir = Path(project_dir)
        self.state_machine = state_machine or StateMachine(self.project_dir)
        self.console = console or Console()
        self.chapters_dir = self.project_dir / "chapters"
        self.archived_dir = self.chapters_dir / "_archived"

    def rollback_to_chapter(self, target_chapter: int) -> RollbackResult:
        """回滚到指定章节（F10.1）

        将第 target_chapter 章及之后的章节归档到 chapters/_archived/，
        并将进度指针回退到 target_chapter - 1。

        Args:
            target_chapter: 目标章节号（从该章重新写起，1-based）

        Returns:
            RollbackResult

        Raises:
            ValueError: target_chapter 非法 / 无可归档章节
        """
        if target_chapter < 1:
            raise ValueError(f"目标章节号必须 ≥ 1，收到 {target_chapter}")

        self.state_machine.load()
        if self.state_machine.state != State.WRITING:
            raise ValueError(
                f"回滚仅在 WRITING 状态可用，当前状态: {self.state_machine.state.value}"
            )

        progress = dict(self.state_machine.progress or {})
        total_written = int(progress.get("total_written", 0))

        # 边界：目标章节 > 已写章节，无需回滚
        if target_chapter > total_written:
            return RollbackResult(
                success=False,
                target_chapter=target_chapter,
                old_progress=progress,
                new_progress=progress,
                message=f"目标章节 {target_chapter} 超过已写章节 {total_written}，无需回滚",
            )

        if target_chapter == next_chapter(progress):
            # 回滚最后一章（等于删除最后一章重写）；P2：章号走统一推导
            pass

        # 归档章节文件（target_chapter 及之后）
        archived: list[str] = []
        if self.chapters_dir.exists():
            self.archived_dir.mkdir(parents=True, exist_ok=True)
            # 带时间戳的归档子目录，避免多次回滚覆盖
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            rollback_archive = self.archived_dir / f"rollback_to_{target_chapter}_{timestamp}"
            # 若同一秒内多次回滚，追加计数器避免覆盖
            counter = 1
            while rollback_archive.exists():
                rollback_archive = self.archived_dir / f"rollback_to_{target_chapter}_{timestamp}_{counter}"
                counter += 1
            rollback_archive.mkdir(parents=True, exist_ok=False)

            for ch_file in sorted(self.chapters_dir.glob("ch*.md")):
                ch_num = self._parse_chapter_num(ch_file.name)
                if ch_num is not None and ch_num >= target_chapter:
                    dest = rollback_archive / ch_file.name
                    shutil.move(str(ch_file), str(dest))
                    archived.append(ch_file.name)

        # 更新进度
        old_progress = dict(progress)
        new_total = target_chapter - 1
        progress["total_written"] = new_total
        progress["current_chapter"] = new_total
        progress["last_rollback_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        progress["last_rollback_target"] = target_chapter

        self.state_machine.progress = progress
        self.state_machine.save()

        # ---- 2026-09-09：回滚同步 RAG 索引 ----
        # 被归档章节的切片必须从索引中清除，否则重写时会召回已判废的旧版正文
        # （幽灵召回），且 source 指向已不存在的文件。无索引时 no-op（自举由写章侧负责）。
        self._sync_rag_index(archived)

        # ---- 2026-09-15：回滚同步章节指纹库（同族：辅助索引必须随内容失效）----
        # 指纹库不清理时，重写同号章节会与「自己的上一版」命中，产生
        # 「相似度 1.00 疑似跨章重复」假阳性（灵荒薪传 ch025 实证）→ 打回重写，
        # 加剧回退振荡。无指纹库时 no-op。
        self._sync_fingerprints(archived)

        return RollbackResult(
            success=True,
            target_chapter=target_chapter,
            archived_chapters=archived,
            old_progress=old_progress,
            new_progress=progress,
            message=(
                f"已回滚到第 {target_chapter} 章（保留 1-{new_total}，"
                f"归档 {len(archived)} 章）"
            ),
        )

    @staticmethod
    def _parse_chapter_num(filename: str) -> int | None:
        """从文件名解析章节号，如 ch003.md → 3"""
        m = re.match(r"ch(\d+)\.md$", filename)
        if m:
            return int(m.group(1))
        return None

    def _sync_rag_index(self, archived: list[str]) -> None:
        """回滚后清除被归档章节的 RAG 切片（失败只告警，绝不阻断回滚）

        Args:
            archived: 已归档的章节文件名列表（chNNN.md）
        """
        rag_dir = self.project_dir / ".state" / "rag"
        if not rag_dir.exists():
            return
        nums = [n for n in (self._parse_chapter_num(f) for f in archived) if n]
        if not nums:
            return
        try:
            from agent.core.rag.indexer import Indexer

            removed = Indexer(self.project_dir).drop_chapters(nums)
            if removed:
                self.console.print(
                    f"[dim]· RAG 索引已同步：清除 {len(nums)} 章 / {removed} 切片[/dim]"
                )
        except Exception as e:  # noqa: BLE001 - 索引同步失败不影响回滚本身
            self.console.print(
                f"[yellow]⚠ RAG 索引同步失败（不影响回滚）：{e}[/yellow]"
            )  # noqa: SILENT_DEGRADE

    def _sync_fingerprints(self, archived: list[str]) -> None:
        """回滚后清除被归档章节的指纹（失败只告警，绝不阻断回滚）。

        2026-09-15（灵荒薪传 ch025「相似度 1.00 跨章重复」假阳性复盘）：
        ``.state/chapter_fingerprints.json`` 原先不随回滚清理，被归档章号的旧指纹
        仍留在库里；重写同号章节时新稿会与**自己的上一版**比对命中，被判「跨章
        重复」→ 打回重写 → 再回退，形成振荡（与 :meth:`_sync_rag_index` 同族：
        **辅助索引必须随内容一起失效**，否则过期索引会产出假结论）。

        Args:
            archived: 已归档的章节文件名列表（chNNN.md）
        """
        nums = [n for n in (self._parse_chapter_num(f) for f in archived) if n]
        if not nums:
            return
        fp_file = self.project_dir / ".state" / "chapter_fingerprints.json"
        if not fp_file.exists():
            return
        try:
            from agent.core.quality.guardrails import load_fingerprints, save_fingerprints

            db = load_fingerprints(fp_file)
            removed = 0
            for n in nums:
                for key in (str(n), f"ch{n:03d}", f"ch{n}"):
                    if key in db:
                        del db[key]
                        removed += 1
            if removed:
                save_fingerprints(db, fp_file)
                self.console.print(
                    f"[dim]· 章节指纹已同步：清除 {removed} 章指纹[/dim]"
                )
        except Exception as e:  # noqa: BLE001 - 指纹同步失败不影响回滚本身
            degrade(
                "m10_rollback.sync_fingerprints",
                "章节指纹库同步失败（不影响回滚），重写同号章节可能与旧指纹撞车",
                e,
            )

    def list_archived(self) -> list[Path]:
        """列出所有归档目录"""
        if not self.archived_dir.exists():
            return []
        return sorted(
            d for d in self.archived_dir.iterdir() if d.is_dir()
        )


# ============================================================
# F10.2 续作恢复
# ============================================================
@dataclass
class ResumeBrief:
    """续作简报"""

    last_chapter: int
    last_subline: str
    last_written_at: str
    mode: str
    # 3 条悬而未决的剧情线
    pending_plots: list[str] = field(default_factory=list)
    # 未回收伏笔（按优先级）
    unresolved_foreshadows: list[dict[str, Any]] = field(default_factory=list)
    # 关系网最近变化
    relation_changes: list[str] = field(default_factory=list)
    # 建议下一步
    suggestions: list[str] = field(default_factory=list)

    def to_markdown(self) -> str:
        lines: list[str] = []
        lines.append("# 续作简报")
        lines.append("")
        lines.append("## 上次进度")
        lines.append("")
        lines.append(f"- **最后章节**：第 {self.last_chapter} 章")
        lines.append(f"- **当前支线**：{self.last_subline}")
        lines.append(f"- **最后写作时间**：{self.last_written_at}")
        lines.append(f"- **介入模式**：{self.mode}")
        lines.append("")

        lines.append("## 悬而未决的剧情线")
        lines.append("")
        if self.pending_plots:
            for i, p in enumerate(self.pending_plots, 1):
                lines.append(f"{i}. {p}")
        else:
            lines.append("（无）")
        lines.append("")

        lines.append("## 未回收伏笔")
        lines.append("")
        if self.unresolved_foreshadows:
            lines.append("| ID | 内容 | 预期回收点 |")
            lines.append("|---|---|---|")
            for f in self.unresolved_foreshadows:
                lines.append(
                    f"| {f.get('id', '')} | {f.get('content', '')} | {f.get('expected_resolve', '')} |"
                )
        else:
            lines.append("（无未回收伏笔）")
        lines.append("")

        lines.append("## 关系网最近变化")
        lines.append("")
        if self.relation_changes:
            for c in self.relation_changes:
                lines.append(f"- {c}")
        else:
            lines.append("（无近期变化）")
        lines.append("")

        lines.append("## 建议下一步")
        lines.append("")
        if self.suggestions:
            for i, s in enumerate(self.suggestions, 1):
                lines.append(f"{i}. {s}")
        else:
            lines.append("继续写下一章")
        lines.append("")

        return "\n".join(lines)


@workflow("m10_resume")
class M10ResumeWorkflow:
    """续作恢复工作流（F10.2）

    用法：
        wf = M10ResumeWorkflow(project_dir)
        brief = wf.generate_brief()
        print(brief.to_markdown())
    """

    def __init__(
        self,
        project_dir: Path,
        state_machine: StateMachine | None = None,
        console: Console | None = None,
    ) -> None:
        self.project_dir = Path(project_dir)
        self.state_machine = state_machine or StateMachine(self.project_dir)
        self.console = console or Console()

    def generate_brief(self) -> ResumeBrief:
        """生成续作简报

        Returns:
            ResumeBrief
        """
        self.state_machine.load()
        progress = self.state_machine.progress or {}
        last_chapter = int(progress.get("total_written", 0))
        last_subline = str(progress.get("current_subline", "未知"))
        last_written_at = str(progress.get("last_written_at", "未知"))
        mode = self.state_machine.mode

        # 1. 悬而未决的剧情线
        pending_plots = self._collect_pending_plots(last_subline)

        # 2. 未回收伏笔
        unresolved_foreshadows = self._collect_unresolved_foreshadows()

        # 3. 关系网变化
        relation_changes = self._collect_relation_changes()

        # 4. 建议
        suggestions = self._build_suggestions(
            last_chapter, last_subline, pending_plots, unresolved_foreshadows
        )

        return ResumeBrief(
            last_chapter=last_chapter,
            last_subline=last_subline,
            last_written_at=last_written_at,
            mode=mode,
            pending_plots=pending_plots[:3],  # 最多 3 条
            unresolved_foreshadows=unresolved_foreshadows,
            relation_changes=relation_changes[:5],  # 最多 5 条
            suggestions=suggestions,
        )

    def _collect_pending_plots(self, current_subline: str) -> list[str]:
        """收集悬而未决的剧情线"""
        plots: list[str] = []

        # 当前支线信息
        subline_file = self.project_dir / "sublines" / current_subline / "subline.md"
        if subline_file.exists():
            post = frontmatter.load(subline_file)
            goal = post.metadata.get("goal", "")
            if goal:
                plots.append(f"当前支线「{current_subline}」目标：{goal}")

        # 路线节点
        route_file = self.project_dir / "protagonist_route.md"
        if route_file.exists():
            content = route_file.read_text(encoding="utf-8")
            # 找未完成的节点（简单启发式：找 milestone 关键词）
            # 这里用简单文本扫描
            lines = content.split("\n")
            for line in lines:
                if "milestone" in line.lower() or "里程碑" in line:
                    # 截取前 80 字
                    snippet = line.strip()[:80]
                    if snippet:
                        plots.append(f"路线节点：{snippet}")
                    if len(plots) >= 5:
                        break

        # 未完成支线
        sublines_dir = self.project_dir / "sublines"
        if sublines_dir.exists():
            for d in sorted(sublines_dir.iterdir()):
                if not d.is_dir() or d.name == current_subline:
                    continue
                plots.append(f"待推进支线：{d.name}")

        return plots

    def _collect_unresolved_foreshadows(self) -> list[dict[str, Any]]:
        """收集未回收伏笔"""
        foreshadow_file = self.project_dir / "foreshadows.md"
        if not foreshadow_file.exists():
            return []

        content = foreshadow_file.read_text(encoding="utf-8")
        results: list[dict[str, Any]] = []

        # 解析 markdown 表格行
        # 格式: | F-01 | 内容 | 埋设点 | 预期回收 | 状态 | ...
        for line in content.split("\n"):
            line = line.strip()
            if not line.startswith("|") or line.startswith("|---") or line.startswith("| ID"):
                continue
            cells = [c.strip() for c in line.split("|")[1:-1]]
            if len(cells) < 5:
                continue
            fid, fcontent, planted, expected, state = cells[:5]
            # 只保留未回收的
            if state in ("已回收", "已废弃"):
                continue
            # 逾期优先
            results.append({
                "id": fid,
                "content": fcontent,
                "planted_at": planted,
                "expected_resolve": expected,
                "state": state,
            })

        # 按状态排序：逾期 > 未埋 > 已埋
        priority = {"逾期": 0, "未埋": 1, "已埋": 2}
        results.sort(key=lambda x: priority.get(x.get("state", ""), 9))
        return results

    def _collect_relation_changes(self) -> list[str]:
        """收集关系网最近变化（archived 边）"""
        graph_file = self.project_dir / "relations" / "graph.md"
        if not graph_file.exists():
            return []

        content = graph_file.read_text(encoding="utf-8")
        changes: list[str] = []

        # 找 archived 标记
        for line in content.split("\n"):
            line = line.strip()
            if "archived" in line.lower():
                # 截取前 100 字
                changes.append(line[:100])

        return changes

    def _build_suggestions(
        self,
        last_chapter: int,
        current_subline: str,
        pending_plots: list[str],
        unresolved_foreshadows: list[dict[str, Any]],
    ) -> list[str]:
        """构建下一步建议"""
        suggestions: list[str] = []

        # 基本建议
        suggestions.append(f"继续写第 {last_chapter + 1} 章（支线 {current_subline}）")

        # 伏笔建议
        overdue = [f for f in unresolved_foreshadows if f.get("state") == "逾期"]
        if overdue:
            suggestions.append(
                f"⚠ 有 {len(overdue)} 条逾期伏笔，建议优先安排回收"
            )
        elif unresolved_foreshadows:
            suggestions.append(
                f"有 {len(unresolved_foreshadows)} 条未回收伏笔，可考虑在近期章节埋设或回收"
            )

        # 剧情线建议
        if len(pending_plots) > 1:
            suggestions.append("有多条悬而未决的剧情线，建议聚焦当前支线推进")

        return suggestions

    def show_brief(self, brief: ResumeBrief) -> None:
        """在终端展示续作简报"""
        console = self.console

        # 上次进度面板
        console.print(
            Panel(
                f"[bold]最后章节[/bold]：第 {brief.last_chapter} 章\n"
                f"[bold]当前支线[/bold]：{brief.last_subline}\n"
                f"[bold]最后写作[/bold]：{brief.last_written_at}\n"
                f"[bold]介入模式[/bold]：{brief.mode}",
                title="续作简报",
                border_style="cyan",
            )
        )

        # 悬而未决的剧情线
        if brief.pending_plots:
            console.print("\n[bold]悬而未决的剧情线[/bold]")
            for i, p in enumerate(brief.pending_plots, 1):
                console.print(f"  [cyan]{i}.[/cyan] {p}")

        # 未回收伏笔
        if brief.unresolved_foreshadows:
            console.print("\n[bold]未回收伏笔[/bold]")
            table = Table(show_lines=False)
            table.add_column("ID", style="cyan")
            table.add_column("内容", style="white")
            table.add_column("状态", style="yellow")
            table.add_column("预期回收", style="dim")
            for f in brief.unresolved_foreshadows:
                table.add_row(
                    f["id"], f["content"][:40], f["state"], f["expected_resolve"]
                )
            console.print(table)

        # 关系网变化
        if brief.relation_changes:
            console.print("\n[bold]关系网最近变化[/bold]")
            for c in brief.relation_changes:
                console.print(f"  [dim]•[/dim] {c}")

        # 建议
        if brief.suggestions:
            console.print("\n[bold green]建议下一步[/bold green]")
            for i, s in enumerate(brief.suggestions, 1):
                console.print(f"  [green]{i}.[/green] {s}")
