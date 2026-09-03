from __future__ import annotations

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *

@command(global_=True)
def load_skill(
    name: str = typer.Argument(..., help="skill 名称（如 bookworm）"),
) -> None:
    """M15 加载 skill - 注册该 skill 提供的命令

    内置 skill：
      - bookworm：书虫测评（注册 /bookworm-review）

    使用示例：
      novel-agent load-skill bookworm
    """
    from agent.client.gateway_adapter import create_gateway_adapter

    registry = _get_registry()
    try:
        skill = registry.load_builtin(name, llm=create_gateway_adapter(), console=console)
    except ValueError as e:
        console.print(f"[bold red]✗[/bold red] {e}")
        raise typer.Exit(code=1) from e

    commands = skill.manifest.command_names
    console.print(
        f"[bold green]✓ skill '{name}' 已加载[/bold green] "
        f"(v{skill.manifest.version}) · 注册命令: {', '.join('/' + c for c in commands)}"
    )
