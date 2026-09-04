from __future__ import annotations

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *

@command(global_=True)
def import_draft(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    source_file: str = typer.Option(
        ..., "--file", "-f", help="草稿文件路径（txt/markdown）"
    ),
    no_characters: bool = typer.Option(
        False, "--no-characters", help="不生成角色档案，仅生成 world.md"
    ),
) -> None:
    """M11 导入 - 从草稿反向构建设定集（命令名自动转为 import-draft）

    F11.1：用户上传已有草稿（txt/markdown），Agent 反向解析并构建：
      - world.md（世界观/简介/力量体系）
      - characters/*.md（角色档案，可使用 --no-characters 跳过）

    使用示例：
      novel-agent import-draft -d projects/my-novel -f ./my_draft.txt
      novel-agent import-draft -d projects/my-novel -f ./draft.md --no-characters
    """
    from pathlib import Path

    from agent.client.gateway_adapter import create_gateway
    from agent.workflows.evaluation.m11_export import ImportWorkflow

    project_path = Path(project_dir)
    enforce_gate(str(project_path), "import_draft")
    src = Path(source_file)
    if not src.exists():
        console.print(f"[bold red]✗[/bold red] 草稿文件不存在: {src}")
        raise typer.Exit(code=1)

    project_path.mkdir(parents=True, exist_ok=True)

    # 接线：LLM 调用事件 → <project>/.events/events.jsonl（复用公共接线，避免复制）
    from agent.core.event_sourcing.llm_wiring import wire_llm_event_hook

    wire_llm_event_hook(project_path)

    try:
        wf = ImportWorkflow(project_path, llm=create_gateway(), console=console)
        result = wf.import_draft(src, with_characters=not no_characters)
    except Exception as e:
        console.print(f"[bold red]✗ 导入失败[/bold red] {e}")
        raise typer.Exit(code=1) from e

    if result.success:
        console.print(f"[bold green]✓ 导入成功[/bold green] {result.message}")
        if result.world_file:
            console.print(f"[dim]world.md: {result.world_file}[/dim]")
        for cf in result.character_files:
            console.print(f"[dim]角色: {cf.name}[/dim]")
    else:
        console.print(f"[yellow]{result.message}[/yellow]")
