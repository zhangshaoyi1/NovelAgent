from __future__ import annotations

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *

@command(global_=True)
def snapshot(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    label: str = typer.Option(
        "manual", "--label", "-l", help="快照标签（如 before-m2-revision）"
    ),
) -> None:
    """M7 设定集快照 - 创建版本快照

    复制 world.md / sublines / characters / relations 到
    settings_snapshots/<timestamp>_<label>/，用于后续回滚。
    """
    from pathlib import Path

    from agent.core.setting_manager import SettingManager

    project_path = Path(project_dir)
    enforce_gate(str(project_path), "snapshot")
    if not (project_path / "world.md").exists():
        console.print(f"[bold red]✗[/bold red] world.md 不存在")
        raise typer.Exit(code=1)

    sm = SettingManager(project_path)
    snap_dir = sm.create_snapshot(label=label)
    console.print(
        f"[bold green]✓ 快照已创建[/bold green] {snap_dir.relative_to(project_path)}"
    )
