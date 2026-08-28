from __future__ import annotations

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *

from agent.core.engine.state_machine import State

@command(allowed_states=(State.ARCH_CONFIRMED, State.OUTLINING))
def outline(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
) -> None:
    """M3 大纲生成 - 故事简介 + 顶层支线任务列表

    支持在 OUTLINING 状态重跑（此前生成降级/截断时重新生成大纲）。
    基于已确认架构生成 outline.md，并为每条支线创建 subline.md。
    架构未确认时拒绝执行（F14 门禁）。

    Args:
        project_dir: 小说项目目录
    """
    from pathlib import Path

    from agent.workflows.m3_outline import M3OutlineWorkflow

    project_path = Path(project_dir)
    enforce_gate(str(project_path), "outline")
    if not (project_path / "world.md").exists():
        console.print(
            f"[bold red]✗[/bold red] {project_path / 'world.md'} 不存在，请先运行 start"
        )
        raise typer.Exit(code=1)

    workflow = M3OutlineWorkflow(project_dir=project_path)
    try:
        result = workflow.run()
        console.print(
            f"\n[bold green]✓ M3 完成[/bold green] 共 {len(result.sublines)} 条顶层支线"
        )
    except Exception as e:
        console.print(f"\n[bold red]✗ M3 失败[/bold red] {e}")
        raise typer.Exit(code=1) from e
