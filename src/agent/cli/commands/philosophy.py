from __future__ import annotations

from rich.console import Console
from rich.panel import Panel

from agent.cli._app import app, command, console
from agent.core.story.philosophy import render_text


@command(global_=True)
def philosophy() -> None:
    """查看 NovelAgent 的「世界模拟」设计哲学（对标笔枢世界模拟叙事）

    世界先于文字 · 专家而非通才 · 主动权在你；
    对抗设定崩塌与记忆流失——我们给你一台让故事自洽涌现的引擎。
    """
    c = console
    c.print(Panel(render_text(), title="[bold cyan]世界模拟 · 设计哲学[/bold cyan]", border_style="cyan", expand=False))
