"""M8 介入频率控制工作流

基于 PRD F8.1-F8.2，实现三档介入模式：

heavy（重度协作）：
    - 每章生成前询问用户方向（本章重点/想表达的情节/特殊要求）
    - 每章生成后等待用户反馈（满意/修订/重写）
    - 适合：精细化创作、关键剧情节点

light（轻度介入）：
    - 仅在剧情节点（支线切换、压力曲线高潮、伏笔回收点）介入
    - 普通章节自动推进
    - 适合：日常推进、节奏稳定期

auto（自主推进）：
    - 全自动连续生成，仅在重大决策点打断
    - 重大决策：架构修订、路线调整、金手指突破上限、伏笔强制回收
    - 适合：灵感爆发期、批量产出

使用方式：
    1. CLI `/mode heavy|light|auto` 切换
    2. M5 章节创作工作流通过 ModeController 判断是否需要暂停询问用户
    3. 模式持久化到 .state/state.json 的 mode 字段
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from agent.core.state_machine import StateMachine


class Mode(str, Enum):
    """介入频率模式"""

    HEAVY = "heavy"
    LIGHT = "light"
    AUTO = "auto"


# ============================================================
# 介入点定义
# ============================================================
class InterventionPoint(str, Enum):
    """介入点类型（用于判断是否需要询问用户）"""

    CHAPTER_BEFORE = "chapter_before"  # 章节生成前
    CHAPTER_AFTER = "chapter_after"  # 章节生成后
    PLOT_NODE = "plot_node"  # 剧情节点（支线切换、高潮）
    MAJOR_DECISION = "major_decision"  # 重大决策（架构/路线/金手指）
    FORESHADOW_RECALL = "foreshadow_recall"  # 伏笔回收点


# 每个模式下各介入点是否需要询问用户
MODE_INTERVENTION_MATRIX: dict[Mode, set[InterventionPoint]] = {
    Mode.HEAVY: {
        InterventionPoint.CHAPTER_BEFORE,
        InterventionPoint.CHAPTER_AFTER,
        InterventionPoint.PLOT_NODE,
        InterventionPoint.MAJOR_DECISION,
        InterventionPoint.FORESHADOW_RECALL,
    },
    Mode.LIGHT: {
        InterventionPoint.PLOT_NODE,
        InterventionPoint.MAJOR_DECISION,
        InterventionPoint.FORESHADOW_RECALL,
    },
    Mode.AUTO: {
        InterventionPoint.MAJOR_DECISION,
    },
}


@dataclass
class ModeInfo:
    """模式信息"""

    mode: Mode
    label: str
    description: str
    intervention_points: list[str]


@dataclass
class M8ModeResult:
    """M8 模式切换结果"""

    old_mode: Mode
    new_mode: Mode
    changed: bool
    message: str


# ============================================================
# 模式控制器
# ============================================================
class ModeController:
    """介入频率模式控制器

    职责：
        - 读取/切换当前模式（持久化到 state.json）
        - 判断给定介入点是否需要询问用户
        - 提供模式信息查询
        - 在 M5 章节创作中接入介入逻辑
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
        self.state_machine.load()

    # ------ 查询 ------
    @property
    def current_mode(self) -> Mode:
        """获取当前模式"""
        try:
            return Mode(self.state_machine.mode)
        except ValueError:
            return Mode.HEAVY

    def should_intervene(self, point: InterventionPoint) -> bool:
        """判断给定介入点在当前模式下是否需要询问用户

        Args:
            point: 介入点类型

        Returns:
            True 表示需要暂停询问用户
        """
        return point in MODE_INTERVENTION_MATRIX.get(self.current_mode, set())

    def get_mode_info(self, mode: Mode | None = None) -> ModeInfo:
        """获取模式详细信息"""
        target = mode or self.current_mode
        info_map = {
            Mode.HEAVY: ModeInfo(
                mode=Mode.HEAVY,
                label="重度协作",
                description="每章生成前问方向、每章生成后等反馈",
                intervention_points=["章节前", "章节后", "剧情节点", "重大决策", "伏笔回收"],
            ),
            Mode.LIGHT: ModeInfo(
                mode=Mode.LIGHT,
                label="轻度介入",
                description="仅剧情节点介入（支线切换/高潮/伏笔回收）",
                intervention_points=["剧情节点", "重大决策", "伏笔回收"],
            ),
            Mode.AUTO: ModeInfo(
                mode=Mode.AUTO,
                label="自主推进",
                description="全自动连续生成，仅重大决策打断",
                intervention_points=["重大决策"],
            ),
        }
        return info_map[target]

    # ------ 切换 ------
    def switch(self, new_mode: str | Mode) -> M8ModeResult:
        """切换模式

        Args:
            new_mode: 目标模式（字符串或枚举）

        Returns:
            切换结果

        Raises:
            ValueError: 非法模式
        """
        if isinstance(new_mode, str):
            try:
                target = Mode(new_mode.lower())
            except ValueError as e:
                raise ValueError(
                    f"非法模式：{new_mode}，可选值：{[m.value for m in Mode]}"
                ) from e
        else:
            target = new_mode

        old = self.current_mode
        if target == old:
            return M8ModeResult(
                old_mode=old,
                new_mode=target,
                changed=False,
                message=f"当前已是 {target.value} 模式，无需切换",
            )

        self.state_machine.set_mode(target.value)
        return M8ModeResult(
            old_mode=old,
            new_mode=target,
            changed=True,
            message=f"模式已切换：{old.value} → {target.value}",
        )

    # ------ 介入交互（M5 调用） ------
    def ask_chapter_direction(self, ctx: dict[str, Any]) -> str | None:
        """章节生成前询问用户方向（heavy 模式）

        Args:
            ctx: M5 上下文（含 chapter_num, subline_id, route_milestone 等）

        Returns:
            用户输入的方向描述，None 表示跳过
        """
        if not self.should_intervene(InterventionPoint.CHAPTER_BEFORE):
            return None

        info = self.get_mode_info()
        self.console.print(
            Panel(
                f"即将生成第 {ctx.get('chapter_num', '?')} 章\n"
                f"支线：{ctx.get('subline_id', '?')}\n"
                f"节点：{ctx.get('route_milestone', '?')}\n"
                f"压力阶段：{ctx.get('pressure_stage', '?')}\n\n"
                f"[模式：{info.label}] 请输入本章方向（回车跳过由 Agent 自主决定）：",
                title="[cyan]章节方向确认[/cyan]",
                border_style="cyan",
                expand=False,
            )
        )
        try:
            direction = Prompt.ask(
                "本章方向",
                default="",
                show_default=False,
                console=self.console,
            )
        except (EOFError, KeyboardInterrupt, OSError):
            return None
        return direction.strip() or None

    def ask_chapter_feedback(self, ctx: dict[str, Any], result: dict[str, Any]) -> str:
        """章节生成后等待用户反馈（heavy 模式）

        Args:
            ctx: M5 上下文
            result: 生成结果（含 word_count, quality_passed 等）

        Returns:
            用户决策：accept / revise / rewrite / continue
        """
        if not self.should_intervene(InterventionPoint.CHAPTER_AFTER):
            return "continue"

        status = "通过" if result.get("quality_passed") else "未完全通过"
        self.console.print(
            Panel(
                f"第 {ctx.get('chapter_num', '?')} 章已生成\n"
                f"字数：{result.get('word_count', 0)} | 质量：{status}\n\n"
                "请选择：",
                title="[green]章节反馈[/green]",
                border_style="green",
                expand=False,
            )
        )
        try:
            choice = Prompt.ask(
                "操作",
                choices=["accept", "revise", "rewrite", "continue"],
                default="accept",
                console=self.console,
            )
            return choice
        except (EOFError, KeyboardInterrupt, OSError):
            return "accept"

    def notify_plot_node(self, ctx: dict[str, Any]) -> bool:
        """剧情节点通知（light/heavy 模式）

        Args:
            ctx: 节点信息（含 node_type, description）

        Returns:
            True 表示用户确认继续，False 表示用户要调整
        """
        if not self.should_intervene(InterventionPoint.PLOT_NODE):
            return True

        self.console.print(
            Panel(
                f"节点类型：{ctx.get('node_type', '?')}\n"
                f"描述：{ctx.get('description', '?')}\n\n"
                "即将进入下一阶段，是否继续？",
                title="[yellow]剧情节点[/yellow]",
                border_style="yellow",
                expand=False,
            )
        )
        try:
            return Confirm.ask("继续推进", default=True, console=self.console)
        except (EOFError, KeyboardInterrupt, OSError):
            return True

    def notify_major_decision(self, ctx: dict[str, Any]) -> bool:
        """重大决策通知（所有模式都介入）

        Args:
            ctx: 决策信息（含 decision_type, description, options）

        Returns:
            True 表示用户确认，False 表示用户拒绝
        """
        self.console.print(
            Panel(
                f"决策类型：{ctx.get('decision_type', '?')}\n"
                f"描述：{ctx.get('description', '?')}\n"
                f"建议选项：{ctx.get('options', [])}\n\n"
                "这是重大决策，需要您确认：",
                title="[bold red]⚠ 重大决策[/bold red]",
                border_style="red",
                expand=False,
            )
        )
        try:
            return Confirm.ask("确认执行", default=False, console=self.console)
        except (EOFError, KeyboardInterrupt, OSError):
            return False

    # ------ 展示 ------
    def show_status(self) -> None:
        """展示当前模式信息"""
        mode = self.current_mode
        info = self.get_mode_info()
        table = Table(title="介入频率模式")
        table.add_column("项目", style="cyan")
        table.add_column("值", style="white")
        table.add_row("当前模式", f"{mode.value}（{info.label}）")
        table.add_row("描述", info.description)
        table.add_row("介入点", "、".join(info.intervention_points))
        self.console.print(table)

    def show_all_modes(self) -> None:
        """展示所有可选模式"""
        table = Table(title="可选介入模式")
        table.add_column("模式", style="cyan")
        table.add_column("标签", style="white")
        table.add_column("描述", style="white")
        table.add_column("介入点", style="green")
        for m in Mode:
            info = self.get_mode_info(m)
            marker = " ← 当前" if m == self.current_mode else ""
            table.add_row(
                m.value + marker,
                info.label,
                info.description,
                "、".join(info.intervention_points),
            )
        self.console.print(table)
