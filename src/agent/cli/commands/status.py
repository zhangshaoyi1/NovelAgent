from __future__ import annotations

import json as _json
from pathlib import Path

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *

from agent.core.command_router import commands_for_state
from agent.core.state_machine import State


@command(global_=True)
def status(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    json_output: bool = typer.Option(
        False, "--json",
        help="以 JSON 形式输出结果到 stdout（未初始化时输出错误信封，退出码 0）",
    ),
) -> None:
    """查看当前项目状态（状态机位置、进度、模式、可用命令）

    读取 .state/state.json，展示：
      - 当前状态
      - 介入频率模式
      - 进度信息（已写章节数等）
      - 当前状态下可用的命令

    --json 输出字段：state / mode / progress / available_commands（available_commands
    保留带斜杠的 CommandMeta.name，如 "/write"，与命令注册表一致）。
    """
    project_path = Path(project_dir)
    enforce_gate(str(project_path), "status", json_mode=json_output)
    state_file = project_path / ".state" / "state.json"

    if not json_output:
        console.print(f"[bold]项目目录[/bold]: {project_path}")

    if not state_file.exists():
        if json_output:
            emit_result(
                {
                    "success": False,
                    "error": {
                        "code": "not_initialized",
                        "message": (
                            f"未找到状态文件（{state_file}），项目尚未初始化；"
                            f"运行 novel-agent start -d {project_dir} 开新书"
                        ),
                    },
                },
                json_mode=True,
            )
            return
        console.print(
            f"[yellow]未找到状态文件（{state_file}），项目尚未初始化[/yellow]\n"
            f"提示：运行 [cyan]novel-agent start -d {project_dir}[/cyan] 开新书"
        )
        return

    data = _json.loads(state_file.read_text(encoding="utf-8"))
    state_val = data.get("state", "INIT")
    mode = data.get("mode", "heavy")
    progress = data.get("progress", {})

    if json_output:
        emit_result(
            {
                "state": state_val,
                "mode": mode,
                "progress": progress,
                "available_commands": [c.name for c in commands_for_state(state_val)],
            },
            json_mode=True,
        )
        return

    # 状态展示
    try:
        state = State(state_val)
        state_label = state.value
    except ValueError:
        state_label = state_val

    console.print(f"[bold]当前状态[/bold]: [green]{state_label}[/green]")
    console.print(f"[bold]介入模式[/bold]: [cyan]{mode}[/cyan]")

    # 进度
    if progress:
        console.print("[bold]进度[/bold]:")
        for k, v in progress.items():
            console.print(f"  {k}: {v}")
    else:
        console.print("[dim]进度: 暂无[/dim]")

    # 可用命令
    allowed = commands_for_state(state_val)
    if allowed:
        table = Table(title=f"当前状态可用命令（{state_label}）")
        table.add_column("命令", style="cyan")
        table.add_column("说明", style="white")
        table.add_column("用法", style="dim")
        for cmd in allowed:
            table.add_row(cmd.name, cmd.description, cmd.usage)
        console.print(table)
    else:
        console.print("[yellow]当前状态无可用命令[/yellow]")
