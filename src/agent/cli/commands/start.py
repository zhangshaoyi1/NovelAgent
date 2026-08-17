from __future__ import annotations

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *

from agent.core.state_machine import State

@command(allowed_states=(State.INIT,))
def start(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
) -> None:
    """开新书 - 进入 M1 启动配置工作流

    交互式收集标题/体量/题材/风格/故事核心，
    调用 LLM 生成世界观，渲染并保存 world.md。

    Args:
        project_dir: 小说项目工作区目录
    """
    from pathlib import Path

    from agent.workflows.m1_config import M1ConfigWorkflow

    project_path = Path(project_dir)
    enforce_gate(str(project_path), "start")
    project_path.mkdir(parents=True, exist_ok=True)

    workflow = M1ConfigWorkflow(project_dir=project_path)
    try:
        result = workflow.run()
        console.print(
            f"\n[bold green]✓ M1 完成[/bold green] world.md 已生成：{result.world_file}"
        )
    except Exception as e:
        console.print(f"\n[bold red]✗ M1 失败[/bold red] {e}")
        raise typer.Exit(code=1) from e
