from __future__ import annotations

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *

from agent.core.engine.state_machine import State

@command(allowed_states=(State.WRITING,))
def rollback(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    chapter: int = typer.Option(
        ..., "--chapter", "-c", help="回滚到第 N 章（从该章重新写起，1-based）"
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="跳过二次确认"
    ),
) -> None:
    """M10 分叉点回滚 - 回滚到指定章节重新写起

    F10.1：将第 N 章及之后的章节归档到 chapters/_archived/（不删除），
    并将进度指针回退到第 N-1 章。

    状态要求：WRITING

    使用示例：
      # 回滚到第 20 章（保留 1-19，归档 20+）
      novel-agent rollback -d projects/my-novel -c 20

      # 跳过确认
      novel-agent rollback -d projects/my-novel -c 20 -y
    """
    from pathlib import Path

    from agent.workflows.evaluation.m10_rollback import M10RollbackWorkflow

    project_path = Path(project_dir)
    enforce_gate(str(project_path), "rollback")
    if not (project_path / ".state" / "state.json").exists():
        console.print(f"[bold red]✗[/bold red] 状态文件不存在，项目未初始化")
        raise typer.Exit(code=1)

    wf = M10RollbackWorkflow(project_path, console=console)

    # 二次确认
    if not yes:
        from rich.prompt import Confirm

        console.print(
            f"[yellow]即将回滚到第 {chapter} 章[/yellow]\n"
            f"第 {chapter} 章及之后的章节会被移动到 chapters/_archived/（不删除）"
        )
        try:
            if not Confirm.ask("确认回滚？", default=False):
                console.print("[yellow]已取消[/yellow]")
                return
        except (EOFError, OSError):
            console.print("[yellow]非交互环境，使用 -y 跳过确认[/yellow]")
            raise typer.Exit(code=1) from None

    try:
        result = wf.rollback_to_chapter(chapter)
    except ValueError as e:
        console.print(f"[bold red]✗[/bold red] {e}")
        raise typer.Exit(code=1) from e

    if result.success:
        console.print(f"[bold green]✓ 回滚成功[/bold green] {result.message}")
        if result.archived_chapters:
            console.print(f"[dim]归档章节：{', '.join(result.archived_chapters)}[/dim]")
    else:
        console.print(f"[yellow]{result.message}[/yellow]")
