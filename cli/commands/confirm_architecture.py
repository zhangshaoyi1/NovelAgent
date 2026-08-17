from __future__ import annotations

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *

from agent.core.state_machine import State

@command(allowed_states=(State.ARCHITECTING, State.ARCH_REVISION,))
def confirm_architecture(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="跳过二次确认（用于脚本/CI）"
    ),
) -> None:
    """M14 确认故事架构 - 写入 confirmed: true 并解锁下游

    确认前会显示将解锁的下游阶段作为轻量防误。

    Args:
        project_dir: 小说项目目录
        yes: 跳过交互式二次确认
    """
    from pathlib import Path

    from agent.workflows.m14_architecture import M14ArchitectureWorkflow

    project_path = Path(project_dir)
    enforce_gate(str(project_path), "confirm_architecture")
    workflow = M14ArchitectureWorkflow(project_dir=project_path)
    if yes:
        workflow.with_confirm_yes(True)
    try:
        result = workflow.confirm()
        if result.confirmed:
            console.print(
                f"\n[bold green]✓ 架构已确认[/bold green] "
                f"已解锁：{', '.join(result.unlocked_stages)}"
            )
        else:
            console.print("\n[yellow]已取消确认[/yellow]")
    except Exception as e:
        console.print(f"\n[bold red]✗ 确认失败[/bold red] {e}")
        raise typer.Exit(code=1) from e
