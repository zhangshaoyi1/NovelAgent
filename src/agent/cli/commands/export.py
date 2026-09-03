from __future__ import annotations

import os
from pathlib import Path

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *

from agent.core.engine.state_machine import State

@command(allowed_states=(State.WRITING, State.COMPLETED,))
def export(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    format: str = typer.Option(
        "markdown", "--format", "-f", help="导出格式: txt | markdown | epub"
    ),
    output_dir: str = typer.Option(
        "", "--output", "-o", help="输出目录（默认 <project>/exports/）"
    ),
    title: str = typer.Option(
        "", "--title", "-t", help="书名（默认从 world.md 读取）"
    ),
    json_output: bool = typer.Option(
        False, "--json",
        help="以 JSON 形式输出结果到 stdout；仍按 --format 写导出文件（两者正交）",
    ),
    env_file: str = typer.Option(
        None, "--env",
        help="指定 .env 文件（仅本次命令生效，透传给下游 GatewayAdapter）",
    ),
) -> None:
    """M11 导出 - 导出为 TXT/Markdown/EPUB

    F11.2：完结或中途导出全部章节。

    --json 与 --format 正交：--json 只决定输出 JSON 到 stdout；--format 仍决定
    导出文件格式（即便 --json 也会按 --format 写文件）。

    使用示例：
      # 导出为 markdown
      novel-agent export -d projects/my-novel -f markdown

      # 导出为 txt，指定书名
      novel-agent export -d projects/my-novel -f txt -t "我的修仙路"

      # 导出为 epub
      novel-agent export -d projects/my-novel -f epub -o ./output
    """
    from agent.workflows.m11_export import ExportWorkflow

    # D：--env 透传
    if env_file:
        os.environ["NOVEL_AGENT_DOTENV"] = env_file

    project_path = Path(project_dir)
    enforce_gate(str(project_path), "export", json_mode=json_output)
    wf_console = make_quiet_console() if json_output else console
    wf = ExportWorkflow(project_path, console=wf_console)

    out_dir = Path(output_dir) if output_dir else None
    book_title = title if title else None

    try:
        result = wf.export(format, output_dir=out_dir, title=book_title)
    except ValueError as e:
        if json_output:
            emit_result(
                {
                    "success": False,
                    "error": {"code": "export_failed", "message": str(e)},
                },
                json_mode=True,
            )
        else:
            console.print(f"[bold red]✗[/bold red] {e}")
        raise typer.Exit(code=1) from e
    except ImportError as e:
        if json_output:
            emit_result(
                {
                    "success": False,
                    "error": {"code": "export_failed", "message": str(e)},
                },
                json_mode=True,
            )
        else:
            console.print(f"[bold red]✗[/bold red] {e}")
        raise typer.Exit(code=1) from e

    if json_output:
        emit_result(
            {
                "success": result.success,
                "chapters": result.chapter_count,
                # 命名映射：底层 ExportResult.total_words → JSON key 用 PRD 字段名 total_chars
                "total_chars": result.total_words,
                "output_file": str(result.output_file) if result.success else "",
            },
            json_mode=True,
        )
        return

    if result.success:
        console.print(
            f"[bold green]✓ 导出成功[/bold green] {result.message}\n"
            f"[dim]输出文件: {result.output_file}[/dim]"
        )
    else:
        console.print(f"[yellow]{result.message}[/yellow]")
