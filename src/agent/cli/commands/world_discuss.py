from __future__ import annotations

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *


@command(global_=True)
def world_discuss(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    message: str = typer.Option(
        "", "--message", "-m", help="作者本轮讨论发言（非交互单轮）"
    ),
    apply: bool = typer.Option(
        False, "--apply", help="把讨论结论合并进 world.md（保留 frontmatter，覆盖正文）"
    ),
) -> None:
    """世界观讨论 - 与 Agent 讨论世界观设定，可合并结论回 world.md

    world.md 生成后、进入脉络讨论前的讨论环节：讨论记录追加到
    world_discussion.md；--apply 按讨论结论重写 world.md 正文。
    不驱动状态机，world.md 存在即可用。

    Args:
        project_dir: 小说项目工作区目录
        message: 本轮讨论发言
        apply: 是否把讨论结论合并进 world.md
    """
    from pathlib import Path

    from agent.workflows.planning.world_discuss import WorldDiscussWorkflow

    project_path = Path(project_dir)
    enforce_gate(str(project_path), "world-discuss")

    if not message.strip() and not apply:
        console.print(
            "[bold red]✗[/bold red] 请提供 --message 发送讨论内容，或使用 --apply 应用讨论结论"
        )
        raise typer.Exit(code=1)

    workflow = WorldDiscussWorkflow(project_dir=project_path)
    try:
        result = workflow.run(message=message, apply=apply)
        if result.agent_reply and not result.applied:
            console.print(
                f"\n[bold green]✓ 世界观讨论完成[/bold green] 记录：{result.discussion_file}"
            )
        if result.applied:
            console.print(
                "[dim]请审阅更新后的 world.md，确认无误后点「确认本阶段」或运行下一阶段。[/dim]"
            )
    except Exception as e:
        console.print(f"\n[bold red]✗ 世界观讨论失败[/bold red] {e}")
        raise typer.Exit(code=1) from e
