"""review-book 命令 —— 成书质量评审（M21，多视角对抗式评审）

对已有章节运行多视角对抗式评审：full（结构架构/设定一致性/读者市场吸引力/埋线与伏笔 4 视角）
/ lean（结构+一致性 2 视角）/ solo（1 视角综合），并综合裁决，生成评审报告到
``{project_dir}/.state/review/review-*.md``。只读分析，不改动任何产物文件。

用法：
    # 全量评审（默认 full + general）
    novel-agent review-book -d <dir>
    # 只审前 10 章，番茄平台 rubric，lean 模式
    novel-agent review-book -d <dir> --scope 1-10 --mode lean --platform fanqie
    # 只看最新一章，输出 JSON
    novel-agent review-book -d <dir> --scope latest --mode solo --json
"""
from __future__ import annotations

import os
from pathlib import Path

from agent.cli._app import app, console, typer
from agent.cli._shared import enforce_gate, emit_result, make_quiet_console
from agent.cli.registry import command


@command(global_=True)
def review_book(
    project_dir: str = typer.Option(
        "novels/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    scope: str = typer.Option(
        "all", "--scope", "-s",
        help="评审范围：all / latest / 1-10 / 1,3,5（逗号/区间混用）",
    ),
    mode: str = typer.Option(
        "full", "--mode", "-m",
        help="评审模式：full（4 视角）/ lean（2 视角）/ solo（1 视角综合）",
    ),
    platform: str = typer.Option(
        "general", "--platform", "-p",
        help="目标平台 rubric：fanqie / qidian / zhihu / general",
    ),
    json_output: bool = typer.Option(
        False, "--json", help="以 JSON 形式输出结果到 stdout"
    ),
    env_file: str = typer.Option(
        None, "--env", help="指定 .env 文件（透传下游 GatewayAdapter）"
    ),
) -> None:
    """M21 成书质量评审 —— 多视角对抗式评审 + 综合裁决（只读，不改产物）"""
    if env_file:
        os.environ["NOVEL_AGENT_DOTENV"] = env_file

    project_path = Path(project_dir)
    if not (project_path / "chapters").exists():
        if json_output:
            emit_result(
                {"success": False, "error": {"code": "no_chapters",
                                             "message": f"{project_path / 'chapters'} 不存在"}},
                json_mode=True,
            )
        else:
            console.print(f"[bold red]✗[/bold red] {project_path / 'chapters'} 不存在")
        raise typer.Exit(code=1)

    enforce_gate(str(project_path), "review_book", json_mode=json_output)

    workflow_console = make_quiet_console() if json_output else console

    from agent.core.event_sourcing.llm_wiring import wire_llm_event_hook
    from agent.workflows.m21_review import M21ReviewWorkflow

    wire_llm_event_hook(project_path)
    wf = M21ReviewWorkflow(project_dir=project_path, console=workflow_console)

    try:
        report = wf.review(scope=scope, mode=mode, platform=platform)
    except Exception as e:
        if json_output:
            emit_result(
                {"success": False, "error": {"code": "review_failed", "message": str(e)}},
                json_mode=True,
            )
        else:
            console.print(f"[bold red]✗ 评审失败[/bold red] {e}")
        raise typer.Exit(code=1) from e

    if json_output:
        emit_result(
            {
                "success": True,
                **report.to_dict(),
                "report_file": str(report.report_file) if report.report_file else None,
            },
            json_mode=True,
        )
        return

    _render_summary(report)
    if report.report_file:
        console.print(f"[dim]报告已保存：{report.report_file}[/dim]")


def _render_summary(report) -> None:
    """终端呈现评审摘要（非 --json 模式）。"""
    from rich.panel import Panel
    from rich.table import Table

    color = {
        "APPROVE": "green",
        "CONCERNS": "yellow",
        "REJECT": "red",
    }.get(report.overall_verdict, "yellow")
    block = sum(1 for i in report.issues if i.severity == "block")
    warn = sum(1 for i in report.issues if i.severity == "warn")
    console.print(
        Panel(
            f"[bold {color}]{report.overall_verdict}[/bold {color}] · "
            f"{report.total_score}/100 · 问题 block {block} / warn {warn}\n"
            f"[italic]{report.verdict_text}[/italic]",
            title=(
                f"成书质量评审 · {report.mode} · {report.platform} · {report.scope}"
            ),
            border_style=color,
        )
    )

    if report.dimensions:
        table = Table(title="分视角评审", show_lines=False)
        table.add_column("视角", style="cyan")
        table.add_column("结论", justify="center")
        table.add_column("问题数", justify="right")
        for d in report.dimensions:
            c = {"APPROVE": "green", "CONCERNS": "yellow", "REJECT": "red"}.get(
                d.verdict, "yellow"
            )
            table.add_row(d.label, f"[{c}]{d.verdict}[/{c}]", str(len(d.issues)))
        console.print(table)

    if report.issues:
        console.print("\n[bold]问题清单[/bold]")
        for i in report.issues:
            if i.severity == "block":
                icon, style = "🚫", "bold red"
            else:
                icon, style = "⚠️", "yellow"
            loc = f" [dim]({i.location})[/dim]" if i.location else ""
            console.print(f"  {icon} [{style}]{i.description}[/{style}]{loc}")

    if report.recommendations:
        console.print("\n[bold green]修改建议[/bold green]")
        for n, r in enumerate(report.recommendations, 1):
            console.print(f"  [green]{n}.[/green] {r}")
