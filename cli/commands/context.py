from __future__ import annotations

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *

@command(global_=True)
def context(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    chapter: int = typer.Option(..., "--chapter", "-c", help="章节号"),
    subline: str = typer.Option("", "--subline", help="支线 ID"),
    on_demand: bool = typer.Option(
        True, "--on-demand/--essential-only",
        help="是否包含按需层（默认包含）",
    ),
) -> None:
    """M12 上下文加载 - 加载分层上下文用于章节生成

    F12.3：分层加载（必载层 + 按需层）。
      - 必载层：world.md 摘要 + 当前 subline.md + 角色档案 + 关系网子图
      - 按需层：历史章节摘要、其他支线、伏笔条目

    使用示例：
      novel-agent context -d projects/my-novel -c 10
      novel-agent context -d projects/my-novel -c 10 --essential-only
    """
    from pathlib import Path

    from agent.core.llm_client import LLMClient
    from agent.workflows.m12_audit import ContextLoader

    project_path = Path(project_dir)
    enforce_gate(str(project_path), "context")
    loader = ContextLoader(project_path, llm=LLMClient(), console=console)
    subline_id = subline if subline else None
    try:
        ctx = loader.load_full_context(
            chapter_num=chapter,
            subline_id=subline_id,
            include_on_demand=on_demand,
        )
    except Exception as e:
        console.print(f"[bold red]✗ 上下文加载失败[/bold red] {e}")
        raise typer.Exit(code=1) from e

    essential = ctx.get("essential", ctx)
    console.print("[bold cyan]=== 必载层 ===[/bold cyan]")
    console.print(f"[dim]章节号:[/dim] {essential.get('chapter_num')}")
    console.print(f"[dim]支线:[/dim] {essential.get('subline_name') or essential.get('subline_id')}")
    console.print(f"[dim]角色数:[/dim] {len(essential.get('characters', []))}")
    if essential.get("world_summary"):
        console.print(
            Panel(
                essential["world_summary"][:500],
                title="world.md 摘要",
                border_style="blue",
            )
        )

    if on_demand and "on_demand" in ctx:
        od = ctx["on_demand"]
        console.print("\n[bold cyan]=== 按需层 ===[/bold cyan]")
        if "history" in od:
            console.print(f"[dim]历史:[/dim] {len(od['history'])} 字符")
        if "other_sublines" in od:
            console.print(f"[dim]其他支线:[/dim] {len(od['other_sublines'])} 个")
        if "foreshadows" in od:
            console.print(f"[dim]伏笔表:[/dim] {len(od['foreshadows'])} 字符")
