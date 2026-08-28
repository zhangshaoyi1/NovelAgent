from __future__ import annotations

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *

from agent.core.engine.state_machine import State

@command(allowed_states=(State.ARCHITECTING,))
def architecture(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    feedback: str = typer.Option(
        "", "--feedback", "-f", help="修改意见（非空则迭代，空则生成初稿）"
    ),
) -> None:
    """M14 故事架构 - 生成初稿或基于反馈迭代

    无 --feedback：生成 architecture.md 初稿（confirmed: false）
    有 --feedback：基于修改意见迭代架构（version +1）

    Args:
        project_dir: 小说项目目录
        feedback: 修改意见
    """
    from pathlib import Path

    from agent.workflows.m14_architecture import M14ArchitectureWorkflow

    project_path = Path(project_dir)
    # 门禁：初稿生成须在 ARCHITECTING 阶段；带 --feedback 的迭代修订对任意状态放行
    #（由 workflow 校验 architecture.md 已存在），满足「任何阶段都能按意见修改架构」。
    if not feedback:
        enforce_gate(str(project_path), "architecture")
    if not (project_path / "world.md").exists() and not feedback:
        console.print(
            f"[bold red]✗[/bold red] {project_path / 'world.md'} 不存在，请先运行 start"
        )
        raise typer.Exit(code=1)

    workflow = M14ArchitectureWorkflow(project_dir=project_path)
    try:
        if feedback:
            result = workflow.iterate(feedback)
            console.print(
                f"\n[bold green]✓ M14 迭代完成[/bold green] v{result.version}"
            )
        else:
            result = workflow.generate()
            console.print(
                f"\n[bold green]✓ M14 初稿已生成[/bold green] v{result.version}"
            )
    except Exception as e:
        console.print(f"\n[bold red]✗ M14 失败[/bold red] {e}")
        raise typer.Exit(code=1) from e
