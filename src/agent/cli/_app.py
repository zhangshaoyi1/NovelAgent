"""CLI 应用实例与共享控制台（从原 cli.py 拆出）

所有命令模块共享同一个 typer.Typer 实例与 rich.Console。

注意：模块名用 _app.py 而非 app.py，避免被包属性 `app`（Typer 实例）
在 `from agent.cli.app import app` 时被同名属性遮蔽。
"""

from __future__ import annotations

import typer
from rich.console import Console

app = typer.Typer(
    name="novel-agent",
    help="共创式小说写作 Agent - 设定集驱动的长篇一致性 + 剧集树 + 关系演化",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
console = Console()

# 命令单一注册点装饰器（T-1）：命令模块 ``from agent.cli._app import command``
from agent.cli.registry import command  # noqa: E402,F401
