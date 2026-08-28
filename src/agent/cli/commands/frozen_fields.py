from __future__ import annotations

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *

@command(global_=True)
def frozen_fields(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
) -> None:
    """M7 冻结字段管理 - 查看当前冻结字段"""
    from pathlib import Path

    import frontmatter

    from agent.core.story.setting_manager import SettingManager

    project_path = Path(project_dir)
    enforce_gate(str(project_path), "frozen_fields")
    world_file = project_path / "world.md"
    if not world_file.exists():
        console.print("[bold red]✗[/bold red] world.md 不存在")
        raise typer.Exit(code=1)

    post = frontmatter.load(world_file)
    frozen = post.metadata.get("frozen_fields", []) or []
    sm = SettingManager(project_path)

    table = Table(title="冻结字段")
    table.add_column("字段", style="cyan")
    table.add_column("状态", style="white")
    for field in frozen:
        status = "[green]已解冻[/green]" if not sm.is_frozen(field) else "[red]冻结[/red]"
        table.add_row(field, status)
    console.print(table)
