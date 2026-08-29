from __future__ import annotations

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *

from agent.core.engine.state_machine import State

@command(allowed_states=(State.INIT,))
def start(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    title: str = typer.Option(
        "", "--title", help="小说标题（非空则走非交互配置，供 Web UI / 脚本使用）"
    ),
    scope: str = typer.Option(
        "long", "--scope", help="体量: short(短篇) | medium(中篇) | long(长篇) | mega(百万字) | custom(自定义)"
    ),
    total_words: int = typer.Option(
        0, "--total-words", help="自定义体量（--scope custom）目标总字数（字，必填）"
    ),
    chapter_length: int = typer.Option(
        0, "--chapter-length", help="单章字数（字）；custom 必填，要求 1500-5000，推荐 2000-2500"
    ),
    genre: str = typer.Option(
        "xiuxian", "--genre", help="题材（单值，向后兼容；多题材请用 --genres）"
    ),
    genres: str = typer.Option(
        "", "--genres", help="题材（可多选，逗号分隔，如 xiuxian,wuxia；需与题材包名一致）"
    ),
    story_core: str = typer.Option(
        "", "--story-core", help="故事核心（一句话）"
    ),
) -> None:
    """开新书 - 进入 M1 启动配置工作流

    交互式收集标题/体量/题材/风格/故事核心，
    调用 LLM 生成世界观，渲染并保存 world.md。
    提供 --title 等选项可走非交互模式（Web UI / 脚本调用）。
    支持多题材混搭：--genres xiuxian,wuxia（题材设定将自动合并，冲突可经 /merge-genres 裁决）。

    Args:
        project_dir: 小说项目工作区目录
        title: 标题（非空触发非交互）
        scope: 体量
        genre: 题材（单值，向后兼容）
        genres: 题材（多值，逗号分隔）
        story_core: 故事核心
    """
    from pathlib import Path

    from agent.workflows.m1_config import M1ConfigWorkflow, M1Input

    project_path = Path(project_dir)
    enforce_gate(str(project_path), "start")
    project_path.mkdir(parents=True, exist_ok=True)

    # 解析题材：--genres 优先，否则回退 --genre，最终默认 [xiuxian]
    if genres.strip():
        genre_list = [g.strip() for g in genres.replace("，", ",").split(",") if g.strip()]
    elif genre.strip():
        genre_list = [genre.strip()]
    else:
        genre_list = ["xiuxian"]

    workflow = M1ConfigWorkflow(project_dir=project_path)
    try:
        if title:
            user_input = M1Input(
                title=title,
                scope=scope,
                genres=genre_list,
                story_core=story_core,
                total_words=total_words if total_words > 0 else None,
                chapter_length=chapter_length if chapter_length > 0 else None,
            )
            result = workflow.run(user_input=user_input)
        else:
            result = workflow.run()
        console.print(
            f"\n[bold green]✓ M1 完成[/bold green] world.md 已生成：{result.world_file}"
        )
    except Exception as e:
        console.print(f"\n[bold red]✗ M1 失败[/bold red] {e}")
        raise typer.Exit(code=1) from e
