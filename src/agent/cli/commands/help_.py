from __future__ import annotations

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *

@command(global_=True)
def help_() -> None:
    """别名：列出命令"""
    commands()
