from __future__ import annotations

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *

@command(global_=True)
def foreshadow_check(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    subline: str = typer.Option(
        "", "--subline", "-s",
        help="支线 ID（如 S01_器灵人性觉醒），检查该支线未回收伏笔",
    ),
) -> None:
    """M13 伏笔检查 - 展示仪表盘 + 支线结束检查

    不带参数：展示伏笔统计仪表盘
    带 --subline：检查指定支线的未回收伏笔
    """
    from pathlib import Path

    from agent.workflows.m13_foreshadow import M13ForeshadowWorkflow

    project_path = Path(project_dir)
    enforce_gate(str(project_path), "foreshadow_check")
    if not (project_path / "foreshadows.md").exists():
        console.print(
            f"[bold red]✗[/bold red] {project_path / 'foreshadows.md'} 不存在"
        )
        raise typer.Exit(code=1)

    wf = M13ForeshadowWorkflow(project_dir=project_path, console=console)
    if subline:
        unresolved = wf.check_subline_end(subline)
        if unresolved:
            console.print(
                f"\n[bold yellow]⚠ 支线 {subline} 有 {len(unresolved)} 条未回收伏笔：[/bold yellow]"
            )
            for f in unresolved:
                console.print(f"  - {f.fid}: {f.content} [{f.state}] → 回收点 {f.expected_resolve}")
        else:
            console.print(f"\n[green]✓ 支线 {subline} 无未回收伏笔[/green]")
    else:
        wf.show_dashboard()
