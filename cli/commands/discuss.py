from __future__ import annotations

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *

from agent.core.state_machine import State

@command(allowed_states=(State.CONFIGURING, State.DISCUSSING,))
def discuss(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    max_rounds: int = typer.Option(
        10, "--max-rounds", "-r", help="最大讨论轮次"
    ),
) -> None:
    """M2 脉络讨论 - 多轮追问深化故事思路

    交互式与 Agent 讨论，产出 discussion.md。
    输入 /next 结束讨论。

    Args:
        project_dir: 小说项目目录
        max_rounds: 最大讨论轮次
    """
    from pathlib import Path

    from agent.workflows.m2_discuss import M2DiscussWorkflow, M2Input

    project_path = Path(project_dir)
    enforce_gate(str(project_path), "discuss")
    if not (project_path / "world.md").exists():
        console.print(
            f"[bold red]✗[/bold red] {project_path / 'world.md'} 不存在，请先运行 start"
        )
        raise typer.Exit(code=1)

    workflow = M2DiscussWorkflow(project_dir=project_path)
    try:
        result = workflow.run(user_input=M2Input(max_rounds=max_rounds))
        console.print(
            f"\n[bold green]✓ M2 完成[/bold green] 共 {result.rounds} 轮讨论"
        )
    except Exception as e:
        console.print(f"\n[bold red]✗ M2 失败[/bold red] {e}")
        raise typer.Exit(code=1) from e
