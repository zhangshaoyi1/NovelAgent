from __future__ import annotations

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *

@command(global_=True)
def summarize_range(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    start: int = typer.Option(..., "--start", "-s", help="起始章节号"),
    end: int = typer.Option(..., "--end", "-e", help="结束章节号（含）"),
    force: bool = typer.Option(
        False, "--force", help="强制重新生成（覆盖已有摘要）"
    ),
) -> None:
    """M12 批量章节摘要 - 为一段章节范围生成摘要

    F12.3：批量压缩旧章节为结构化摘要。

    使用示例：
      novel-agent summarize-range -d projects/my-novel -s 1 -e 20
      novel-agent summarize-range -d projects/my-novel -s 1 -e 20 --force
    """
    from pathlib import Path

    from agent.client import LLMClient
    from agent.workflows.m12_audit import ChapterSummarizer

    project_path = Path(project_dir)
    enforce_gate(str(project_path), "summarize_range")
    summarizer = ChapterSummarizer(project_path, llm=LLMClient(), console=console)
    try:
        results = summarizer.summarize_range(start, end, skip_existing=not force)
    except Exception as e:
        console.print(f"[bold red]✗ 批量生成失败[/bold red] {e}")
        raise typer.Exit(code=1) from e

    if not results:
        console.print("[yellow]无新摘要生成（可能已存在或章节不存在）[/yellow]")
        return

    console.print(
        f"[bold green]✓ 已生成 {len(results)} 个摘要[/bold green] "
        f"（章节 {start}-{end}）"
    )
    for s in results:
        console.print(f"[dim]ch{s.chapter_num:03d}: {s.title}[/dim]")
