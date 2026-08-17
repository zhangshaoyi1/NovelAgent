from __future__ import annotations

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *

@command(global_=True)
def genre_info(
    name: str = typer.Argument(..., help="题材包名称（如 xiuxian）"),
) -> None:
    """M9 查看题材包详细信息

    F9.4：题材包声明 world.md 模板片段与质量规则片段。
    本命令展示题材包的能力与资源。

    使用示例：
      novel-agent genre-info xiuxian
    """
    from agent.core.genre_pack import GenrePackRegistry

    registry = GenrePackRegistry()
    try:
        info = registry.info(name)
    except ValueError as e:
        console.print(f"[bold red]✗[/bold red] {e}")
        raise typer.Exit(code=1) from e

    console.print(f"[bold cyan]题材包：{info['name']}[/bold cyan] v{info['version']}")
    console.print(f"[dim]{info['description']}[/dim]")
    console.print()
    console.print("[bold]资源：[/bold]")
    console.print(f"  world 模板: {'✓' if info['has_world_template'] else '✗'}")
    console.print(f"  爽点套路: {'✓' if info['has_tropes'] else '✗'}")
    console.print(f"  术语表: {'✓' if info['has_terms'] else '✗'}")
    console.print(f"  战斗模板: {'✓' if info['has_combat_template'] else '✗'}")
    console.print(f"  质量规则: {'✓' if info['has_quality_rules'] else '✗'}")
    if info["hooks"]:
        console.print(f"  hooks: {', '.join(info['hooks'])}")
    if info["dependencies"]:
        console.print(f"  dependencies: {', '.join(info['dependencies'])}")
