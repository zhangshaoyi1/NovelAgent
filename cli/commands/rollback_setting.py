from __future__ import annotations

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *

@command(global_=True)
def rollback_setting(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    snapshot_name: str = typer.Option(
        ..., "--snapshot", "-s", help="快照目录名（settings_snapshots/ 下的子目录）"
    ),
) -> None:
    """M7 设定集回滚 - 回滚到指定快照

    注意：回滚会覆盖当前 world.md / sublines / characters / relations，
    建议先创建当前状态的快照。
    """
    from pathlib import Path

    from agent.core.setting_manager import SettingManager

    project_path = Path(project_dir)
    enforce_gate(str(project_path), "rollback_setting")
    snap_dir = project_path / "settings_snapshots" / snapshot_name
    if not snap_dir.exists():
        console.print(f"[bold red]✗[/bold red] 快照不存在: {snapshot_name}")
        raise typer.Exit(code=1)

    sm = SettingManager(project_path)
    sm.rollback_to_snapshot(snap_dir)
    console.print(
        f"[bold green]✓ 已回滚到快照[/bold green] {snapshot_name}"
    )
