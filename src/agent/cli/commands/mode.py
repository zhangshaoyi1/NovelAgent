from __future__ import annotations

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *

@command(global_=True)
def mode(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    target: str = typer.Option(
        "", "--target", "-t",
        help="目标模式：heavy / light / auto。留空则仅查看当前模式",
    ),
) -> None:
    """M8 介入频率控制 - 查看/切换模式

    三档模式：
      - heavy：每章前问方向、每章后等反馈（重度协作）
      - light：仅剧情节点介入（轻度介入）
      - auto：自主推进，重大决策才打断（全自动）

    使用示例：
      novel-agent mode -d projects/test-novel              # 查看当前模式
      novel-agent mode -d projects/test-novel -t light     # 切换到 light
      novel-agent mode -d projects/test-novel -t auto      # 切换到 auto
    """
    from pathlib import Path

    from agent.workflows.m8_mode import ModeController

    project_path = Path(project_dir)
    enforce_gate(str(project_path), "mode")
    if not (project_path / ".state").exists():
        console.print(
            f"[bold red]✗[/bold red] {project_path / '.state'} 不存在，请先运行 start"
        )
        raise typer.Exit(code=1)

    controller = ModeController(project_dir=project_path, console=console)
    if not target:
        controller.show_status()
        console.print()
        controller.show_all_modes()
        return

    try:
        result = controller.switch(target)
        if result.changed:
            console.print(f"[bold green]✓[/bold green] {result.message}")
            controller.show_status()
        else:
            console.print(f"[yellow]{result.message}[/yellow]")
    except ValueError as e:
        console.print(f"[bold red]✗[/bold red] {e}")
        raise typer.Exit(code=1) from e
