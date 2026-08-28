from __future__ import annotations

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *

@command(global_=True)
def commands(
    project_dir: str = typer.Option(
        "", "--dir", "-d", help="小说项目目录，提供则按当前状态过滤可用命令",
    ),
) -> None:
    """列出命令清单（F16.2）

    不带 --dir：列出全量命令清单
    带 --dir：按项目当前状态过滤，只显示可用命令
    """
    import json as _json
    from pathlib import Path

    from agent.core.engine.command_router import COMMAND_REGISTRY, commands_for_state

    if not project_dir:
        # 全量清单
        table = Table(title="全量命令清单（未按阶段过滤）")
        table.add_column("#", style="dim")
        table.add_column("命令", style="cyan")
        table.add_column("说明", style="white")
        table.add_column("用法", style="dim")
        for i, cmd in enumerate(COMMAND_REGISTRY, 1):
            table.add_row(str(i), cmd.name, cmd.description, cmd.usage)
        console.print(table)
        console.print(
            "\n[dim]提示：运行 novel-agent commands -d <project> 查看当前状态可用命令[/dim]"
        )
        return

    # 按状态过滤
    project_path = Path(project_dir)
    enforce_gate(str(project_path), "cmd_list")
    state_file = project_path / ".state" / "state.json"
    if not state_file.exists():
        console.print(
            f"[yellow]未找到状态文件（{state_file}），显示全量命令[/yellow]"
        )
        table = Table(title="全量命令清单")
        table.add_column("命令", style="cyan")
        table.add_column("说明", style="white")
        for cmd in COMMAND_REGISTRY:
            table.add_row(cmd.name, cmd.description)
        console.print(table)
        return

    data = _json.loads(state_file.read_text(encoding="utf-8"))
    state_val = data.get("state", "INIT")
    allowed = commands_for_state(state_val)

    table = Table(title=f"当前状态 [{state_val}] 可用命令")
    table.add_column("命令", style="cyan")
    table.add_column("说明", style="white")
    table.add_column("用法", style="dim")
    for cmd in allowed:
        table.add_row(cmd.name, cmd.description, cmd.usage)
    console.print(table)
