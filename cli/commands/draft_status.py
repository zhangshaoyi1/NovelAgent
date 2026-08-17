from __future__ import annotations

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *

@command(global_=True)
def draft_status(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
) -> None:
    """M18 查看草稿状态 - 检测是否存在未完成草稿

    F18.4：写作中断后，未完成的章节草稿保存在 .state/draft.wip。
    本命令查看草稿信息（不自动续写）。
    """
    from pathlib import Path

    from agent.workflows.m18_recovery import DraftManager

    project_path = Path(project_dir)
    enforce_gate(str(project_path), "draft_status")
    dm = DraftManager(project_path)

    if not dm.has_draft():
        console.print("[green]✓ 无未完成草稿[/green]")
        return

    draft = dm.load_draft()
    if draft is None:
        console.print("[yellow]草稿文件存在但无法解析[/yellow]")
        return

    console.print(
        f"[yellow]⚠ 检测到未完成草稿[/yellow]\n"
        f"  章节：第 {draft.chapter_num} 章\n"
        f"  支线：{draft.subline_id}\n"
        f"  保存时间：{draft.saved_at}\n"
        f"  正文长度：{len(draft.text)} 字"
    )
