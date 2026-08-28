from __future__ import annotations

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *

@command(global_=True)
def unfreeze(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    field: str = typer.Option(..., "--field", "-f", help="要解冻的字段名"),
) -> None:
    """M7 冻结字段管理 - 解冻指定字段（仅当前会话有效）"""
    from pathlib import Path

    from agent.core.story.setting_manager import SettingManager

    project_path = Path(project_dir)
    enforce_gate(str(project_path), "unfreeze")
    sm = SettingManager(project_path)
    sm.unfreeze(field)
    console.print(f"[bold green]✓ 字段 '{field}' 已解冻[/bold green]（仅当前会话有效）")
