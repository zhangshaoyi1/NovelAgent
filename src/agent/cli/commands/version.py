from __future__ import annotations

from agent import __version__  # 显式导入：`import *` 不导出下划线开头的名字

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *

@command(global_=True)
def version() -> None:
    """显示版本号"""
    console.print(f"novel-agent v{__version__}")
