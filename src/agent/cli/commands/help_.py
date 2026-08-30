from __future__ import annotations

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *

@command(global_=True)
def help_(
    project_dir: str = typer.Option("", "--dir", "-d", help="小说项目目录，提供则按当前状态过滤可用命令"),
) -> None:
    """列出命令清单（等价于 commands；带 --dir 按状态过滤当前可用命令）"""
    commands(project_dir=project_dir)
