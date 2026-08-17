from __future__ import annotations

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *

@command(global_=True)
def list_snapshots(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
) -> None:
    """M7 设定集快照 - 列出所有快照"""
    from pathlib import Path

    from agent.core.setting_manager import SettingManager

    project_path = Path(project_dir)
    enforce_gate(str(project_path), "list_snapshots")
    sm = SettingManager(project_path)
    snapshots = sm.list_snapshots()
    if not snapshots:
        console.print("[yellow]暂无快照[/yellow]")
        return

    table = Table(title="设定集快照列表")
    table.add_column("#", style="dim")
    table.add_column("快照名称", style="cyan")
    table.add_column("创建时间", style="white")
    for i, snap in enumerate(snapshots, 1):
        # 从目录名解析时间
        name = snap.name
        console.print(f"  {i}. {name}")
    console.print(table)
