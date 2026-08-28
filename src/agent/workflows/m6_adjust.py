"""M6 动态调整工作流

基于 PRD F6.1-F6.4，实现路线/关系动态调整 + 一致性影响报告：

路线调整 (M6AdjustRouteWorkflow)：
    1. 状态门禁：WRITING / CHARACTER_DESIGN
    2. 读 protagonist_route.md + 当前进度（确定当前节点）
    3. LLM 根据用户意图生成新路线（旧主分支→archived_alt，不删除）
    4. 只允许调整"当前节点及未来节点"，已写节点只归档
    5. 渲染新 protagonist_route.md
    6. 生成一致性影响报告

关系调整 (M6AdjustRelationWorkflow)：
    1. 状态门禁：WRITING / CHARACTER_DESIGN
    2. 读 relations/graph.md 结构化节点和边
    3. LLM 根据用户意图调整关系（旧边→archived，强度=0，不删除）
    4. 渲染新 graph.md（含更新的 Mermaid 图 + 节点/边表）
    5. 生成一致性影响报告

一致性影响报告：
    - field_conflicts：与 world.md 冻结字段冲突
    - affected_characters：受影响角色
    - affected_chapters：受影响已写章节
    - golden_finger_risk：金手指登记上限风险
    - timeline_conflicts：时序冲突
    - recommendations：2 种解决选项（改章节 vs 改设定）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from agent.core.engine.workflow_registry import workflow

import frontmatter
from jinja2 import Environment, FileSystemLoader, select_autoescape
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from agent.client import LLMClient
from agent.core.story.setting_manager import SettingManager
from agent.core.engine.state_machine import State, StateMachine
from agent.core.quality.confirmation import is_architecture_confirmed
from agent.prompts import (
    M6_ADJUST_ROUTE_SYSTEM_PROMPT,
    M6_ADJUST_ROUTE_USER_TEMPLATE,
    M6_ADJUST_RELATION_SYSTEM_PROMPT,
    M6_ADJUST_RELATION_USER_TEMPLATE,
    M6_IMPACT_REPORT_SYSTEM_PROMPT,
    M6_IMPACT_REPORT_USER_TEMPLATE,
)
from agent.utils import parse_llm_json

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


# ============================================================
# P1 修复（2026-08-21）：LLM 调用 + JSON 解析，失败自动重试一次
# 截断多为瞬时故障，重试常可恢复；供 M6AdjustRouteWorkflow 与
# M6AdjustRelationWorkflow 共用（模块级函数，避免重复实现）。
# ============================================================
def _chat_parse_with_retry(
    llm,
    console,
    system: str,
    user: str,
    *,
    temperature: float,
    max_tokens: int,
    label: str,
    utility: bool = False,
) -> dict[str, Any]:
    """调用 LLM 并解析 JSON；解析失败自动重试一次。

    Returns:
        解析后的 dict

    Raises:
        ValueError: 重试后仍无法解析为 JSON 对象
    """
    last_text = ""
    for attempt in range(2):
        chat = llm.chat_utility if utility else llm.chat_creative
        raw = chat(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            enable_thinking=False,
        )
        last_text = raw.text
        try:
            data = parse_llm_json(last_text)
            if not isinstance(data, dict):
                raise ValueError("LLM 返回不是 JSON 对象")
            return data
        except ValueError:
            if attempt == 0:
                console.print(
                    f"[yellow]⚠ {label} JSON 解析失败，自动重试一次...[/yellow]"
                )
    raise ValueError(f"{label}：无法解析为 JSON（已自动重试一次仍失败）")


# ============================================================
# 结果数据类
# ============================================================
@dataclass
class M6ImpactReport:
    """一致性影响报告"""

    field_conflicts: list[dict[str, Any]] = field(default_factory=list)
    affected_characters: list[str] = field(default_factory=list)
    affected_chapters: list[str] = field(default_factory=list)
    golden_finger_risk: str = ""
    timeline_conflicts: list[str] = field(default_factory=list)
    recommendations: list[dict[str, str]] = field(default_factory=list)
    raw_report: dict[str, Any] = field(default_factory=dict)

    @property
    def has_conflicts(self) -> bool:
        return bool(
            self.field_conflicts
            or self.affected_chapters
            or self.timeline_conflicts
            or self.golden_finger_risk
        )

    @property
    def severity_count(self) -> dict[str, int]:
        counts: dict[str, int] = {"high": 0, "medium": 0, "low": 0}
        for c in self.field_conflicts:
            sev = str(c.get("severity", "low")).lower()
            if sev in counts:
                counts[sev] += 1
            else:
                counts["low"] += 1
        return counts


@dataclass
class M6RouteResult:
    """路线调整结果"""

    route_file: Path
    old_route_archived: int  # 归档的旧分支数
    new_nodes_count: int
    impact_report: M6ImpactReport
    current_node_id: str
    change_summary: str


@dataclass
class M6RelationResult:
    """关系调整结果"""

    graph_file: Path
    archived_edges_count: int
    new_edges_count: int
    nodes_count: int
    impact_report: M6ImpactReport
    change_summary: str


# ============================================================
# 路线调整工作流
# ============================================================
@workflow("m6_adjust_route")
class M6AdjustRouteWorkflow:
    """M6 主角成长路线动态调整"""

    def __init__(
        self,
        project_dir: Path,
        llm_client: LLMClient | None = None,
        setting_manager: SettingManager | None = None,
        state_machine: StateMachine | None = None,
        console: Console | None = None,
    ) -> None:
        self.project_dir = Path(project_dir)
        self.llm = llm_client or LLMClient()
        self.sm = setting_manager or SettingManager(self.project_dir)
        self.state_machine = state_machine or StateMachine(self.project_dir)
        self.console = console or Console()
        self.jinja_env = Environment(
            loader=FileSystemLoader(str(TEMPLATES_DIR)),
            autoescape=select_autoescape(disabled_extensions=("j2",)),
            keep_trailing_newline=True,
        )

    # ------ 入口 ------
    def run(self, user_intent: str) -> M6RouteResult:
        """执行路线调整

        Args:
            user_intent: 用户描述的调整意图，例如"让主角在N02选择加入执法堂当卧底"

        Raises:
            RuntimeError: 状态不符 / 文件缺失
        """
        self.state_machine.load()
        if self.state_machine.state not in (
            State.CHARACTER_DESIGN,
            State.WRITING,
            State.PAUSED,
        ):
            raise RuntimeError(
                f"当前状态 {self.state_machine.state.value} 不允许调整路线，"
                f"需先进入 WRITING 状态"
            )

        # ★门禁：world.md 必须存在
        world_data = self.sm.load_world()
        if not world_data["exists"]:
            raise RuntimeError("world.md 不存在，请先运行 M1 配置")

        if not is_architecture_confirmed(self.project_dir):
            raise RuntimeError("故事架构尚未确认，无法调整路线")

        route_file = self.project_dir / "protagonist_route.md"
        if not route_file.exists():
            raise RuntimeError("protagonist_route.md 不存在，请先完成 M4")

        # ------ 1. 读取当前路线 + 进度 ------
        self.console.print("\n[cyan]正在加载当前主角路线...[/cyan]")
        current_route_text = route_file.read_text(encoding="utf-8")
        progress = self.state_machine.progress or {}
        current_chapter = int(progress.get("total_written", 0) or 0)

        # 确定当前节点索引（从1开始）
        current_node_idx = self._locate_current_node(
            current_route_text, current_chapter
        )

        # ------ 2. LLM 生成新路线 ------
        self.console.print(
            f"[cyan]正在根据意图调整路线（当前：ch{current_chapter:03d}，N{current_node_idx:02d} 节点）...[/cyan]"
        )
        new_route = self._llm_adjust_route(
            current_route_text, current_chapter, current_node_idx, user_intent
        )

        # ------ 3. 统计归档情况（前置） ------
        old_archived_count = self._count_archived_alts(current_route_text)
        new_route_dict = self._route_to_dict(new_route) if isinstance(new_route, dict) else new_route
        nodes_count = len(new_route_dict.get("nodes", [])) if isinstance(new_route_dict, dict) else 0

        # ------ 4. 渲染新路线文件 ------
        route_path = self._render_route(new_route_dict)

        # ------ 3.5 统一按文本方式统计归档（前后都用同一方法，抵消模板标题的影响） ------
        new_route_text = route_path.read_text(encoding="utf-8")
        new_archived_count = self._count_archived_alts(new_route_text)
        archived_delta = max(0, new_archived_count - old_archived_count)

        change_summary = (
            f"路线调整：用户意图「{user_intent[:40]}」，"
            f"当前节点 N{current_node_idx:02d}，"
            f"归档旧主分支 {archived_delta} 条，"
            f"新路线共 {nodes_count} 个节点"
        )

        # ------ 5. 一致性影响报告 ------
        self.console.print("[cyan]正在生成一致性影响报告...[/cyan]")
        impact = self._generate_impact_report(
            change_summary=change_summary,
            route_snippet=self._extract_future_nodes_snippet(
                new_route_dict, current_node_idx
            ),
            relations_snippet="",
        )

        # ------ 6. 呈现 ------
        self._present_route_result(
            archived_delta, nodes_count, current_node_idx, impact
        )

        return M6RouteResult(
            route_file=route_file,
            old_route_archived=archived_delta,
            new_nodes_count=nodes_count,
            impact_report=impact,
            current_node_id=f"N{current_node_idx:02d}",
            change_summary=change_summary,
        )

    # ------ 内部：定位当前节点 ------
    @staticmethod
    def _locate_current_node(route_text: str, chapter_num: int) -> int:
        """根据章节号定位当前所在的节点索引（从1开始）"""
        blocks = re.split(r"\n## (N\d+)", route_text)
        for i in range(1, len(blocks), 2):
            block = blocks[i + 1] if i + 1 < len(blocks) else ""
            # 兼容 **章节范围**：1-15 和 章节范围：1-15 两种格式
            range_match = re.search(
                r"(?:\*\*)?章节范围(?:\*\*)?[：:]\s*(\d+)[-~](\d+)", block
            )
            if range_match:
                lo = int(range_match.group(1))
                hi = int(range_match.group(2))
                if lo <= max(chapter_num, 1) <= hi:
                    node_id_match = re.match(r"N(\d+)", blocks[i])
                    if node_id_match:
                        return int(node_id_match.group(1))
        return 1

    # ------ 内部：LLM 调整路线 ------
    def _llm_adjust_route(
        self,
        current_route: str,
        current_chapter: int,
        current_node_idx: int,
        user_intent: str,
    ) -> dict[str, Any]:
        user_prompt = M6_ADJUST_ROUTE_USER_TEMPLATE.format(
            current_route=current_route[:3000],
            current_chapter=current_chapter,
            current_node_idx=current_node_idx,
            user_intent=user_intent,
        )
        data = _chat_parse_with_retry(
            self.llm, self.console, M6_ADJUST_ROUTE_SYSTEM_PROMPT, user_prompt,
            temperature=0.7, max_tokens=6000, label="M6 路线调整",
        )
        if "nodes" not in data:
            raise RuntimeError("M6 路线调整：LLM 返回缺少 nodes 字段")
        return data

    # ------ 内部：统计已归档分支 ------
    @staticmethod
    def _count_archived_alts(route_text: str) -> int:
        return len(re.findall(r"archived_alt", route_text))

    @staticmethod
    def _count_archived_alts_in_dict(route_dict: dict[str, Any]) -> int:
        count = 0
        for node in route_dict.get("nodes", []) or []:
            for alt in node.get("alt_branches", []) or []:
                when = str(alt.get("when", ""))
                if "archived_alt" in when:
                    count += 1
        return count

    # ------ 内部：确保 route 是 dict ------
    @staticmethod
    def _route_to_dict(route: Any) -> dict[str, Any]:
        if isinstance(route, dict):
            return route
        return {"root_node": "", "nodes": []}

    # ------ 内部：渲染路线 ------
    def _render_route(self, route: dict[str, Any]) -> Path:
        file = self.project_dir / "protagonist_route.md"
        template = self.jinja_env.get_template("protagonist_route.md.j2")
        content = template.render(
            root_node=route.get("root_node", "") or "路线起点",
            nodes=route.get("nodes", []) or [],
        )
        file.write_text(content, encoding="utf-8")
        return file

    # ------ 内部：提取未来节点摘要 ------
    @staticmethod
    def _extract_future_nodes_snippet(
        route_dict: dict[str, Any], current_node_idx: int
    ) -> str:
        parts: list[str] = []
        for node in route_dict.get("nodes", []) or []:
            nid = str(node.get("id", ""))
            match = re.match(r"N(\d+)", nid)
            idx = int(match.group(1)) if match else 0
            if idx >= current_node_idx:
                main = node.get("main_branch", {}) or {}
                parts.append(
                    f"- {nid} [{node.get('milestone','')}] "
                    f"主分支={main.get('title','')} "
                    f"结果={main.get('result','')[:60]}"
                )
        return "\n".join(parts) if parts else "（无未来节点）"

    # ------ 内部：一致性影响报告 ------
    def _generate_impact_report(
        self,
        change_summary: str,
        route_snippet: str,
        relations_snippet: str,
    ) -> M6ImpactReport:
        # 提取 world.md 冻结字段
        world_frozen = self._extract_world_frozen()
        # 已写章节摘要
        written_chapters = self._summarize_written_chapters()

        user_prompt = M6_IMPACT_REPORT_USER_TEMPLATE.format(
            change_summary=change_summary,
            world_frozen=world_frozen,
            route_snippet=route_snippet,
            relations_snippet=relations_snippet or "（关系调整时填充）",
            written_chapters=written_chapters,
        )
        try:
            data = _chat_parse_with_retry(
                self.llm, self.console, M6_IMPACT_REPORT_SYSTEM_PROMPT, user_prompt,
                temperature=0.2, max_tokens=3000, label="M6 影响报告", utility=True,
            )
        except ValueError:
            return M6ImpactReport(
                recommendations=[
                    {
                        "option": "手动检查",
                        "detail": "自动报告生成失败，请人工核对一致性",
                    }
                ],
                raw_report={"_raw": "(解析失败，已自动重试)"},
            )
        return M6ImpactReport(
            field_conflicts=data.get("field_conflicts", []) or [],
            affected_characters=data.get("affected_characters", []) or [],
            affected_chapters=data.get("affected_chapters", []) or [],
            golden_finger_risk=data.get("golden_finger_risk", "") or "",
            timeline_conflicts=data.get("timeline_conflicts", []) or [],
            recommendations=data.get("recommendations", []) or [],
            raw_report=data,
        )

    def _extract_world_frozen(self) -> str:
        world_data = self.sm.load_world()
        if not world_data["exists"]:
            return "（world.md 未找到）"
        content = world_data.get("content", "")
        parts: list[str] = []
        for sec in ["境界体系", "金手指登记", "天道规则", "地理设定"]:
            s = self._extract_section(content, sec)
            if s:
                parts.append(f"## {sec}\n{s[:500]}")
        return "\n".join(parts) if parts else content[:800]

    @staticmethod
    def _extract_section(content: str, title: str) -> str:
        pat = rf"## {re.escape(title)}\n(.*?)(?=\n## |\Z)"
        m = re.search(pat, content, re.DOTALL)
        return m.group(1).strip() if m else ""

    def _summarize_written_chapters(self) -> str:
        chapters_dir = self.project_dir / "chapters"
        if not chapters_dir.exists():
            return "（无已写章节）"
        files = sorted(chapters_dir.glob("ch*.md"))
        if not files:
            return "（无已写章节）"
        parts: list[str] = []
        for f in files[-5:]:  # 只看最近 5 章
            try:
                post = frontmatter.load(f)
                title = post.metadata.get("title", f.stem)
                nodes = post.metadata.get("route_node", "")
                parts.append(f"- {f.stem}：{title}（节点={nodes}）")
            except Exception:
                parts.append(f"- {f.stem}")
        return "\n".join(parts)

    # ------ 呈现 ------
    def _present_route_result(
        self,
        archived_delta: int,
        nodes_count: int,
        current_node_idx: int,
        impact: M6ImpactReport,
    ) -> None:
        table = Table(title="M6 路线调整结果")
        table.add_column("项目", style="cyan")
        table.add_column("值", style="white")
        table.add_row("当前节点", f"N{current_node_idx:02d}")
        table.add_row("新路线节点数", str(nodes_count))
        table.add_row("归档旧主分支", str(archived_delta) + " 条")
        if impact.has_conflicts:
            sev = impact.severity_count
            table.add_row(
                "⚠ 一致性冲突",
                f"高{sev['high']} 中{sev['medium']} 低{sev['low']}",
            )
        else:
            table.add_row("✓ 一致性", "无冲突")
        self.console.print(table)

        if impact.has_conflicts:
            self._print_conflicts(impact)

    def _print_conflicts(self, impact: M6ImpactReport) -> None:
        self.console.print(
            Panel(
                self._format_impact_text(impact),
                title="[bold yellow]一致性影响报告[/bold yellow]",
                border_style="yellow",
            )
        )

    @staticmethod
    def _format_impact_text(impact: M6ImpactReport) -> str:
        lines: list[str] = []
        if impact.field_conflicts:
            lines.append("[bold]字段冲突：[/bold]")
            for c in impact.field_conflicts:
                lines.append(
                    f"  [{c.get('severity','?').upper()}] "
                    f"{c.get('field','')}: {c.get('in_world','')} → {c.get('after_change','')}"
                )
        if impact.affected_characters:
            lines.append(f"[bold]受影响角色：[/bold]{', '.join(impact.affected_characters)}")
        if impact.affected_chapters:
            lines.append(f"[bold]受影响章节：[/bold]{', '.join(impact.affected_chapters)}")
        if impact.golden_finger_risk:
            lines.append(f"[bold]金手指风险：[/bold]{impact.golden_finger_risk}")
        if impact.timeline_conflicts:
            lines.append("[bold]时序冲突：[/bold]")
            for t in impact.timeline_conflicts:
                lines.append(f"  - {t}")
        if impact.recommendations:
            lines.append("\n[bold]解决方案建议：[/bold]")
            for i, r in enumerate(impact.recommendations, 1):
                lines.append(
                    f"  {i}. {r.get('option','')}：{r.get('detail','')}"
                )
        return "\n".join(lines)


# ============================================================
# 关系调整工作流
# ============================================================
@workflow("m6_adjust_relation")
class M6AdjustRelationWorkflow:
    """M6 角色关系网动态调整"""

    def __init__(
        self,
        project_dir: Path,
        llm_client: LLMClient | None = None,
        setting_manager: SettingManager | None = None,
        state_machine: StateMachine | None = None,
        console: Console | None = None,
    ) -> None:
        self.project_dir = Path(project_dir)
        self.llm = llm_client or LLMClient()
        self.sm = setting_manager or SettingManager(self.project_dir)
        self.state_machine = state_machine or StateMachine(self.project_dir)
        self.console = console or Console()
        self.jinja_env = Environment(
            loader=FileSystemLoader(str(TEMPLATES_DIR)),
            autoescape=select_autoescape(disabled_extensions=("j2",)),
            keep_trailing_newline=True,
        )
        # 复用报告格式化
        self._impact_formatter = M6AdjustRouteWorkflow._format_impact_text

    # ------ 入口 ------
    def run(self, user_intent: str) -> M6RelationResult:
        """执行关系网调整

        Args:
            user_intent: 用户描述的调整意图，例如"赵无极对林寻从对立转为暗中赏识"
        """
        self.state_machine.load()
        if self.state_machine.state not in (
            State.CHARACTER_DESIGN,
            State.WRITING,
            State.PAUSED,
        ):
            raise RuntimeError(
                f"当前状态 {self.state_machine.state.value} 不允许调整关系网，"
                f"需先进入 WRITING 状态"
            )

        # ★门禁：world.md 必须存在
        world_data = self.sm.load_world()
        if not world_data["exists"]:
            raise RuntimeError("world.md 不存在，请先运行 M1 配置")

        if not is_architecture_confirmed(self.project_dir):
            raise RuntimeError("故事架构尚未确认，无法调整关系网")

        graph_file = self.project_dir / "relations" / "graph.md"
        if not graph_file.exists():
            raise RuntimeError("relations/graph.md 不存在，请先完成 M4")

        # ------ 1. 读取 graph.md 结构化数据 ------
        self.console.print("\n[cyan]正在加载当前关系网...[/cyan]")
        graph_content = graph_file.read_text(encoding="utf-8")
        nodes, edges = self._parse_graph_md(graph_content)

        progress = self.state_machine.progress or {}
        current_chapter = int(progress.get("total_written", 0) or 0)

        # ------ 2. LLM 调整关系 ------
        self.console.print(
            f"[cyan]正在根据意图调整关系网（ch{current_chapter:03d}）...[/cyan]"
        )
        new_graph = self._llm_adjust_relation(
            nodes, edges, current_chapter, user_intent
        )
        new_nodes = new_graph.get("nodes", nodes) or []
        new_edges = new_graph.get("edges", edges) or []

        # ------ 3. 统计变更 ------
        archived_before = sum(1 for e in edges if e.get("archived"))
        archived_after = sum(1 for e in new_edges if e.get("archived"))
        archived_delta = max(0, archived_after - archived_before)
        non_archived_before = len(edges) - archived_before
        non_archived_after = len(new_edges) - archived_after
        new_edges_count = max(0, non_archived_after - non_archived_before)
        nodes_count = len(new_nodes)

        # ------ 4. 渲染新 graph.md ------
        self._render_graph(new_nodes, new_edges, current_chapter)

        change_summary = (
            f"关系调整：用户意图「{user_intent[:40]}」，"
            f"当前章节 ch{current_chapter:03d}，"
            f"归档旧关系 {archived_delta} 条，新增关系 {new_edges_count} 条，"
            f"当前关系 {non_archived_after} 条"
        )

        # ------ 5. 一致性影响报告 ------
        self.console.print("[cyan]正在生成一致性影响报告...[/cyan]")
        relations_snippet = self._extract_changed_edges_snippet(
            edges, new_edges
        )
        impact = self._generate_impact_report(
            change_summary=change_summary,
            route_snippet="",
            relations_snippet=relations_snippet,
        )

        # ------ 6. 呈现 ------
        self._present_relation_result(
            archived_delta, new_edges_count, nodes_count, non_archived_after, impact
        )

        return M6RelationResult(
            graph_file=graph_file,
            archived_edges_count=archived_delta,
            new_edges_count=new_edges_count,
            nodes_count=nodes_count,
            impact_report=impact,
            change_summary=change_summary,
        )

    # ------ 内部：解析 graph.md ------
    @staticmethod
    def _parse_graph_md(content: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """从 graph.md 解析 nodes 和 edges"""
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []

        # 解析节点表
        node_sec = re.search(
            r"## 节点\n\n\|.*?\n.*?\n(.*?)(?=\n## |\Z)", content, re.DOTALL
        )
        if node_sec:
            for line in node_sec.group(1).strip().splitlines():
                line = line.strip()
                if not line.startswith("|"):
                    continue
                parts = [p.strip() for p in line.split("|") if p.strip()]
                if len(parts) >= 3:
                    nodes.append(
                        {"id": parts[0], "label": parts[1], "group": parts[2]}
                    )

        # 解析边表
        edge_sec = re.search(
            r"## 边[（(]关系[)）]\n\n\|.*?\n.*?\n(.*?)(?=\n## |\Z)", content, re.DOTALL
        )
        if edge_sec:
            for line in edge_sec.group(1).strip().splitlines():
                line = line.strip()
                if not line.startswith("|"):
                    continue
                parts = [p.strip() for p in line.split("|") if p.strip()]
                if len(parts) >= 5:
                    note = parts[5] if len(parts) >= 6 else ""
                    archived = "archived:" in note.lower()
                    try:
                        intensity = int(parts[3])
                    except ValueError:
                        intensity = 0
                    edges.append(
                        {
                            "from": parts[0],
                            "to": parts[1],
                            "type": parts[2],
                            "intensity": intensity,
                            "since": parts[4],
                            "note": note,
                            "archived": archived,
                        }
                    )

        return nodes, edges

    # ------ 内部：LLM 调整关系 ------
    def _llm_adjust_relation(
        self,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        current_chapter: int,
        user_intent: str,
    ) -> dict[str, Any]:
        # 构造表格文本给 LLM
        nodes_table = "\n".join(
            f"| {n.get('id','')} | {n.get('label','')} | {n.get('group','')} |"
            for n in nodes
        )
        edges_table = "\n".join(
            f"| {e.get('from','')} | {e.get('to','')} | {e.get('type','')} | "
            f"{e.get('intensity',0)} | {e.get('since','')} | {e.get('note','')} | "
            f"{'archived' if e.get('archived') else 'active'} |"
            for e in edges
        )
        user_prompt = M6_ADJUST_RELATION_USER_TEMPLATE.format(
            nodes_table=nodes_table,
            edges_table=edges_table,
            current_chapter=current_chapter,
            user_intent=user_intent,
        )
        data = _chat_parse_with_retry(
            self.llm, self.console, M6_ADJUST_RELATION_SYSTEM_PROMPT, user_prompt,
            temperature=0.7, max_tokens=5000, label="M6 关系调整",
        )
        return data

    # ------ 内部：渲染 graph.md ------
    def _render_graph(
        self,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        current_chapter: int,
    ) -> Path:
        rel_dir = self.project_dir / "relations"
        rel_dir.mkdir(parents=True, exist_ok=True)
        file = rel_dir / "graph.md"
        template = self.jinja_env.get_template("graph.md.j2")
        labels: dict[str, str] = {n.get("id", ""): n.get("label", "") for n in nodes}

        def lookup_label(node_id: str) -> str:
            return labels.get(node_id, node_id)

        # Mermaid 只展示未归档的边（避免图太乱）
        visible_edges = [e for e in edges if not e.get("archived")]

        content = template.render(
            updated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            current_chapter=f"ch{current_chapter:03d}",
            nodes=nodes,
            edges=visible_edges,
            lookup_label=lookup_label,
        )

        # 模板输出后补充归档边表（在边表下方追加）
        archived_edges = [e for e in edges if e.get("archived")]
        if archived_edges:
            content += "\n\n## 归档边（历史关系，不参与渲染）\n\n"
            content += "| 起 | 止 | 原类型 | 原强度 | 起于 | 归档说明 |\n"
            content += "|---|---|---|---|---|---|\n"
            for e in archived_edges:
                content += (
                    f"| {e.get('from','')} | {e.get('to','')} | "
                    f"{e.get('type','')} | {e.get('intensity',0)} | "
                    f"{e.get('since','')} | {e.get('note','')} |\n"
                )

        file.write_text(content, encoding="utf-8")
        return file

    # ------ 内部：提取变更边摘要 ------
    @staticmethod
    def _extract_changed_edges_snippet(
        old_edges: list[dict[str, Any]], new_edges: list[dict[str, Any]]
    ) -> str:
        old_keys = {(e["from"], e["to"], e.get("type", "")) for e in old_edges if not e.get("archived")}
        parts: list[str] = []
        # 新增/变更的
        for e in new_edges:
            if e.get("archived"):
                continue
            key = (e["from"], e["to"], e.get("type", ""))
            if key not in old_keys:
                parts.append(
                    f"+ {e['from']}→{e['to']} [{e.get('type','')}] "
                    f"强度={e.get('intensity',0)} 备注={e.get('note','')[:60]}"
                )
        # 归档的
        for e in new_edges:
            if e.get("archived"):
                if not any(
                    o.get("archived") and o["from"] == e["from"] and o["to"] == e["to"] and o.get("type") == e.get("type")
                    for o in old_edges
                ):
                    parts.append(
                        f"± (archived) {e['from']}→{e['to']} "
                        f"原类型={e.get('type','')} 说明={e.get('note','')[:60]}"
                    )
        return "\n".join(parts) if parts else "（关系无实质变化）"

    # ------ 内部：一致性影响报告（与路线工作流相同逻辑） ------
    def _generate_impact_report(
        self,
        change_summary: str,
        route_snippet: str,
        relations_snippet: str,
    ) -> M6ImpactReport:
        # 复用路线工作流的 report 生成逻辑（通过实例化一个隐藏的 helper）
        helper = M6AdjustRouteWorkflow(
            project_dir=self.project_dir,
            llm_client=self.llm,
            setting_manager=self.sm,
            state_machine=self.state_machine,
            console=self.console,
        )
        world_frozen = helper._extract_world_frozen()
        written_chapters = helper._summarize_written_chapters()

        user_prompt = M6_IMPACT_REPORT_USER_TEMPLATE.format(
            change_summary=change_summary,
            world_frozen=world_frozen,
            route_snippet=route_snippet or "（路线调整时填充）",
            relations_snippet=relations_snippet,
            written_chapters=written_chapters,
        )
        try:
            data = _chat_parse_with_retry(
                self.llm, self.console, M6_IMPACT_REPORT_SYSTEM_PROMPT, user_prompt,
                temperature=0.2, max_tokens=3000, label="M6 影响报告", utility=True,
            )
        except ValueError:
            return M6ImpactReport(
                recommendations=[
                    {
                        "option": "手动检查",
                        "detail": "自动报告生成失败，请人工核对一致性",
                    }
                ],
                raw_report={"_raw": "(解析失败，已自动重试)"},
            )
        return M6ImpactReport(
            field_conflicts=data.get("field_conflicts", []) or [],
            affected_characters=data.get("affected_characters", []) or [],
            affected_chapters=data.get("affected_chapters", []) or [],
            golden_finger_risk=data.get("golden_finger_risk", "") or "",
            timeline_conflicts=data.get("timeline_conflicts", []) or [],
            recommendations=data.get("recommendations", []) or [],
            raw_report=data,
        )

    # ------ 呈现 ------
    def _present_relation_result(
        self,
        archived_delta: int,
        new_edges_count: int,
        nodes_count: int,
        active_edges: int,
        impact: M6ImpactReport,
    ) -> None:
        table = Table(title="M6 关系网调整结果")
        table.add_column("项目", style="cyan")
        table.add_column("值", style="white")
        table.add_row("角色节点数", str(nodes_count))
        table.add_row("活跃关系数", str(active_edges))
        table.add_row("新增关系", f"+{new_edges_count} 条")
        table.add_row("归档旧关系", str(archived_delta) + " 条")
        if impact.has_conflicts:
            sev = impact.severity_count
            table.add_row(
                "⚠ 一致性冲突",
                f"高{sev['high']} 中{sev['medium']} 低{sev['low']}",
            )
        else:
            table.add_row("✓ 一致性", "无冲突")
        self.console.print(table)

        if impact.has_conflicts:
            self.console.print(
                Panel(
                    self._impact_formatter(impact),
                    title="[bold yellow]一致性影响报告[/bold yellow]",
                    border_style="yellow",
                )
            )
