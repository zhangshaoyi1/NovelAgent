from __future__ import annotations

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table

from agent.cli._app import app, command, console
from agent.agents.registry import RosterCategory, get_groups, roster_summary


@command(global_=True)
def roster() -> None:
    """查看 NovelAgent 的「编制完整创作团队」阵容（对标笔枢 30+ 专家 Agent 叙事）

    把真实的引擎模块 / 工作流 / 质量护栏，包装成一支职责明确的专家 Agent 编制：
    世界构建 · 情节叙事 · 成文润色 · 审校把关，四组齐备，由你担任总编。
    """
    c = console
    c.print(Panel(roster_summary(), title="[bold cyan]Agent 阵容[/bold cyan]", border_style="cyan", expand=False))
    c.print("")

    category_style = {
        RosterCategory.WORLD_BUILDING: "green",
        RosterCategory.PLOT_NARRATIVE: "yellow",
        RosterCategory.WRITING_POLISH: "magenta",
        RosterCategory.REVIEW_GUARD: "red",
    }

    for group in get_groups():
        style = category_style[group.category]
        t = Table(title=f"[{style}]{group.category.value}[/{style}] · {group.tagline}", title_justify="left")
        t.add_column("徽", style="bold", width=3, justify="center")
        t.add_column("专家 Agent", style="cyan", no_wrap=True)
        t.add_column("职责", style="white")
        t.add_column("落地引擎", style="dim")
        for a in group.agents:
            t.add_row(a.glyph, a.name, a.responsibility, a.engine)
        c.print(t)
        c.print("")
