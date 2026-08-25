"""compose 命令 —— 一键自动写书（开新书 + 多角色推进至完本）。

与 ``scripts/compose.py`` 共用 ``agent.core.compose_runner`` 的同一套编排逻辑，
因此本命令会自动出现在 Web UI 的通用命令列表（available_commands）中，
满足「新增命令同步进 Web 界面」的约定。

用法：
  novel-agent compose --name "书名" --scope long --genre xuanhuan \
      --story-core "一句话核心梗" --chapters 120
  novel-agent compose -d novels/changan-binyiguan        # 续写已有项目
"""

from __future__ import annotations

import typer

from agent.cli._app import app, command, console
from agent.core.compose_runner import run_compose
from agent.core.state_machine import State


@command(
    name="compose",
    allowed_states=(
        State.INIT, State.CONFIGURING, State.DISCUSSING, State.ARCHITECTING,
        State.ARCH_CONFIRMED, State.OUTLINING, State.CHARACTER_DESIGN, State.WRITING,
    ),
    help="一键自动写书：生成约束文档 → 多角色推进 → 完本（非定时，跑完即止）",
)
def compose(
    name: str = typer.Option("", "--name", help="新书名（非空则开新书）"),
    directory: str = typer.Option(
        "", "--dir", "-d", help="已有项目目录（续写）；与 --name 二选一"
    ),
    scope: str = typer.Option("long", "--scope", help="体量: short|medium|long"),
    genre: str = typer.Option("", "--genre", help="题材，如 xuanhuan/wuxia/xiuxian"),
    story_core: str = typer.Option("", "--story-core", help="一句话故事核心"),
    chapters: int = typer.Option(0, "--chapters", "-n", help="目标章节数(0=默认)"),
    mode: str = typer.Option("auto", "--mode", help="写章引擎档位：auto/heavy/light"),
    env: str = typer.Option("", "--env", help="指定 .env（否则用 agent 默认 .env）"),
    no_checkup: bool = typer.Option(
        False, "--no-checkup", help="写完后不做自动体检（evaluate + foreshadow-report）"
    ),
) -> None:
    """一键自动写书：生成约束文档 → 多角色推进 → 完本（非定时，跑完即止）"""
    rc = run_compose(
        name=name,
        directory=directory,
        scope=scope or "long",
        genre=genre,
        story_core=story_core,
        chapters=chapters,
        mode=mode,
        env=env,
        checkup=not no_checkup,
    )
    if rc != 0:
        raise typer.Exit(code=rc)
