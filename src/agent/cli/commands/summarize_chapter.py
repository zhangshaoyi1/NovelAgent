from __future__ import annotations

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *

@command(global_=True)
def summarize_chapter(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    chapter: int = typer.Option(
        ..., "--chapter", "-c", help="要生成摘要的章节号"
    ),
) -> None:
    """M12 章节摘要 - 为指定章节生成结构化摘要

    F12.3：每写完 N 章，调用 LLM 将旧章节压缩为结构化摘要，
    保存到 chapters/_summaries/ch<NNN>.json。

    使用示例：
      novel-agent summarize-chapter -d projects/my-novel -c 5
    """
    from pathlib import Path

    from agent.client.gateway_adapter import create_gateway
    from agent.workflows.evaluation.m12_audit import ChapterSummarizer

    project_path = Path(project_dir)
    enforce_gate(str(project_path), "summarize_chapter")
    # 接线：LLM 调用事件 → <project>/.events/events.jsonl（复用公共接线，避免复制）
    from agent.core.event_sourcing.llm_wiring import wire_llm_event_hook

    wire_llm_event_hook(project_path)
    summarizer = ChapterSummarizer(project_path, llm=create_gateway(), console=console)
    try:
        summary = summarizer.summarize_chapter(chapter)
    except Exception as e:
        console.print(f"[bold red]✗ 摘要生成失败[/bold red] {e}")
        raise typer.Exit(code=1) from e

    if summary is None:
        console.print(f"[yellow]章节 {chapter} 不存在或生成失败[/yellow]")
        raise typer.Exit(code=1)

    console.print(f"[bold green]✓ 第 {chapter} 章摘要已生成[/bold green]")
    console.print(
        Panel(
            summary.to_markdown(),
            title=f"ch{chapter:03d} - {summary.title}",
            border_style="green",
        )
    )
