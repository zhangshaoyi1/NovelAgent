from __future__ import annotations

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *

@command(global_=True)
def draft_discard(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="跳过确认"
    ),
) -> None:
    """M18 丢弃草稿 - 删除未完成草稿

    F18.4：当草稿已过时或不需要续写时，手动丢弃。
    """
    from pathlib import Path

    from agent.workflows.m18_recovery import DraftManager

    project_path = Path(project_dir)
    enforce_gate(str(project_path), "draft_discard")
    dm = DraftManager(project_path)

    if not dm.has_draft():
        console.print("[green]✓ 无草稿可丢弃[/green]")
        return

    if not yes:
        from rich.prompt import Confirm

        try:
            if not Confirm.ask("确认丢弃草稿？", default=False):
                console.print("[yellow]已取消[/yellow]")
                return
        except (EOFError, OSError):
            pass

    if dm.clear_draft():
        console.print("[bold green]✓ 草稿已丢弃[/bold green]")
    else:
        console.print("[yellow]草稿不存在[/yellow]")
