from __future__ import annotations

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *

@command(global_=True)
def reset_state(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    target: str = typer.Option(
        "", "--target", "-t",
        help="目标状态（如 CONFIGURING/ARCH_CONFIRMED/CHARACTER_DESIGN/WRITING/COMPLETED）。留空则回退到上一稳定状态",
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="跳过二次确认"
    ),
) -> None:
    """M18 状态机卡死恢复 - 重置到上一稳定点

    F18.3：当状态机卡死（如 ARCHITECTING 中断、PAUSED 无法恢复）时，
    重置到上一稳定状态。

    稳定状态：CONFIGURING / ARCH_CONFIRMED / CHARACTER_DESIGN / WRITING / COMPLETED

    使用示例：
      # 回退到上一稳定状态
      novel-agent reset-state -d projects/my-novel

      # 重置到指定状态
      novel-agent reset-state -d projects/my-novel -t ARCH_CONFIRMED

      # 跳过确认（用于脚本）
      novel-agent reset-state -d projects/my-novel -y
    """
    from pathlib import Path

    from agent.workflows.m18_recovery import StateRecovery

    project_path = Path(project_dir)
    enforce_gate(str(project_path), "reset_state")
    state_file = project_path / ".state" / "state.json"
    if not state_file.exists():
        console.print(f"[bold red]✗[/bold red] 状态文件不存在: {state_file}")
        raise typer.Exit(code=1)

    recovery = StateRecovery(project_path)

    # 二次确认
    if not yes:
        from rich.prompt import Confirm

        console.print(
            f"[yellow]即将重置项目状态[/yellow] ({project_path})\n"
            f"当前状态会被备份到 .state/state_history.json"
        )
        try:
            if not Confirm.ask("确认重置？", default=False):
                console.print("[yellow]已取消[/yellow]")
                return
        except (EOFError, OSError):
            console.print("[yellow]非交互环境，使用 -y 跳过确认[/yellow]")
            raise typer.Exit(code=1) from None

    try:
        if target:
            from agent.core.engine.state_machine import State

            target_state = State(target.upper())
            result = recovery.reset_to_state(target_state)
        else:
            result = recovery.reset_to_last_stable()
    except ValueError as e:
        console.print(f"[bold red]✗[/bold red] {e}")
        raise typer.Exit(code=1) from e

    if result.success:
        console.print(f"[bold green]✓ 状态重置成功[/bold green] {result.message}")
        console.print(
            f"[dim]历史已备份: {result.history_file.relative_to(project_path) if result.history_file else 'N/A'}[/dim]"
        )
    else:
        console.print(f"[yellow]{result.message}[/yellow]")
