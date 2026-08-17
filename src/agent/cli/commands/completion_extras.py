from __future__ import annotations

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *

@command(global_=True)
def completion_extras(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    output_dir: str = typer.Option(
        "", "--output", "-o", help="输出目录（默认 <project>/completion/）"
    ),
    skip_afterword: bool = typer.Option(
        False, "--skip-afterword", help="跳过 LLM 生成完本感言（离线场景）"
    ),
) -> None:
    """M11 完本附加产出 - 生成感言/人物志/世界观/伏笔回收报告

    F11.3：完本时生成附加产出：
      - afterword.md（完本感言，LLM 生成，可用 --skip-afterword 跳过）
      - character_anthology.md（人物志，汇总 characters/*.md）
      - world_summary.md（世界观总结，复制 world.md）
      - foreshadow_report.md（伏笔回收报告，复用 M13）

    使用示例：
      novel-agent completion-extras -d projects/my-novel
      novel-agent completion-extras -d projects/my-novel --skip-afterword
    """
    from pathlib import Path

    from agent.core.llm_client import LLMClient
    from agent.workflows.m11_export import CompletionExtrasWorkflow

    project_path = Path(project_dir)
    enforce_gate(str(project_path), "completion_extras")
    if not (project_path / "world.md").exists():
        console.print(f"[bold red]✗[/bold red] 项目未初始化（缺少 world.md）")
        raise typer.Exit(code=1)

    out_dir = Path(output_dir) if output_dir else None

    try:
        wf = CompletionExtrasWorkflow(
            project_path, llm=LLMClient(), console=console
        )
        result = wf.generate(output_dir=out_dir, skip_afterword=skip_afterword)
    except Exception as e:
        console.print(f"[bold red]✗ 生成失败[/bold red] {e}")
        raise typer.Exit(code=1) from e

    console.print(f"[bold green]✓[/bold green] {result.message}")
    for f in (
        result.afterword_file,
        result.character_anthology_file,
        result.world_summary_file,
        result.foreshadow_report_file,
    ):
        if f:
            console.print(f"[dim]{f.name}: {f}[/dim]")
