from __future__ import annotations

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *

@command(global_=True)
def foreshadow_report(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
) -> None:
    """M13 伏笔回收报告 - 生成完结/阶段性伏笔报告

    输出 foreshadow_report.md，包含：
      - 统计总览（总数/未埋/已埋/已回收/已废弃/逾期/回收率）
      - 未回收伏笔清单
      - 逾期未回收预警
      - 处理建议
    """
    from pathlib import Path

    from agent.workflows.evaluation.m13_foreshadow import M13ForeshadowWorkflow

    project_path = Path(project_dir)
    enforce_gate(str(project_path), "foreshadow_report")
    if not (project_path / "foreshadows.md").exists():
        console.print(
            f"[bold red]✗[/bold red] {project_path / 'foreshadows.md'} 不存在"
        )
        raise typer.Exit(code=1)

    wf = M13ForeshadowWorkflow(project_dir=project_path, console=console)
    try:
        report = wf.generate_completion_report()
        console.print(
            f"\n[bold green]✓ M13 伏笔报告已生成[/bold green] "
            f"{report.report_file.relative_to(project_path)}\n"
            f"总 {report.stats.total} 条 | 回收 {report.stats.resolved} | "
            f"逾期 {report.stats.overdue} | 回收率 "
            f"{report.stats.resolve_rate * 100:.1f}%"
        )
        if report.stats.overdue > 0:
            console.print(
                f"[bold red]⚠ {report.stats.overdue} 条伏笔已逾期[/bold red]"
            )
    except Exception as e:
        console.print(f"\n[bold red]✗ M13 失败[/bold red] {e}")
        raise typer.Exit(code=1) from e
