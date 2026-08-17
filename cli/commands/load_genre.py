from __future__ import annotations

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *

@command(global_=True)
def load_genre(
    name: str = typer.Argument(..., help="题材包名称（如 xiuxian）"),
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
) -> None:
    """M9 加载题材包到项目

    F9.1/F9.2：加载指定题材包，将其 world 模板片段写入项目的 world.md 草稿。
    注意：本命令不会覆盖已有的 world.md，只在不存在时创建草稿。

    使用示例：
      novel-agent load-genre xiuxian -d projects/my-novel
    """
    from pathlib import Path

    from agent.core.genre_pack import GenrePackRegistry
    from agent.core.hook_dispatcher import dispatch_genre_hooks

    registry = GenrePackRegistry()
    try:
        pack = registry.load(name)
    except ValueError as e:
        console.print(f"[bold red]✗[/bold red] {e}")
        raise typer.Exit(code=1) from e

    project_path = Path(project_dir)
    enforce_gate(str(project_path), "load_genre")
    project_path.mkdir(parents=True, exist_ok=True)

    # 检查 world.md 是否已存在
    world_file = project_path / "world.md"
    if world_file.exists():
        console.print(
            f"[yellow]⚠ world.md 已存在，未覆盖。[/yellow]\n"
            f"[dim]如需应用题材模板，请手动合并或先删除 world.md[/dim]"
        )
    elif pack.world_template:
        # 写入模板草稿
        world_file.write_text(pack.world_template, encoding="utf-8")
        console.print(
            f"[bold green]✓ 已加载题材包 {name}[/bold green]\n"
            f"[dim]world.md 模板草稿已写入: {world_file}[/dim]"
        )
    else:
        console.print(
            f"[bold green]✓ 已加载题材包 {name}[/bold green]（无 world 模板）"
        )

    # 展示可用资源
    console.print("\n[bold]可用资源：[/bold]")
    if pack.tropes:
        console.print(f"  爽点套路库: {len(pack.tropes)} 字符")
    if pack.terms:
        console.print(f"  术语表: {len(pack.terms)} 字符")
    if pack.combat_template:
        console.print(f"  战斗模板: {len(pack.combat_template)} 字符")
    if pack.quality_rules:
        console.print(f"  质量规则: {len(pack.quality_rules)} 字符")

    # T-3：题材包 hooks 真实执行（注册 world 模板 / 题材层质量规则等）
    dispatched = dispatch_genre_hooks(project_path, name, pack)
    if dispatched:
        console.print(f"[dim]已执行题材 hooks：{', '.join(dispatched)}[/dim]")
