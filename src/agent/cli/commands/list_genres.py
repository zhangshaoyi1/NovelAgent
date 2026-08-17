from __future__ import annotations

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *

@command(global_=True)
def list_genres() -> None:
    """M9 列出所有可用题材包

    F9.2：题材以 skill 形式封装，支持热插拔。
    本命令列出 agent/skills/ 下所有 type=genre 的题材包。

    使用示例：
      novel-agent list-genres
    """
    from agent.core.genre_pack import GenrePackRegistry

    registry = GenrePackRegistry()
    genres = registry.list_available()

    if not genres:
        console.print("[yellow]未找到任何题材包[/yellow]")
        return

    table = Table(title="可用题材包")
    table.add_column("名称", style="cyan")
    table.add_column("版本")
    table.add_column("说明")
    for g in genres:
        table.add_row(g["name"], g["version"], g["description"])
    console.print(table)
