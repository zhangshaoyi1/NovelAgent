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
        help="目标：heavy/light/auto（离散档）或 auto-driver/co-pilot 或 0-100 数值（连续自主度）",
    ),
    autonomy: int = typer.Option(
        None, "--autonomy", "-a", min=0, max=100,
        help="直接设置自主度 0-100（连续滑块）：0=作者全掌控，100=全自动碰撞",
    ),
) -> None:
    """M8 介入频率控制 - 查看/切换「双模式连续滑块」

    离散档（兼容）：
      - heavy：每章前问方向、每章后等反馈（重度协作）
      - light：仅剧情节点介入（轻度介入）
      - auto：自主推进，重大决策才打断（全自动）

    连续预设（对标笔枢）：
      - auto-driver：全自动碰撞（自主度 100，放手让世界状态机自由推演）
      - co-pilot：协同审校（自主度 35，任意节点接管，Agent 辅助改稿）
      - 或任意 0-100 数值：连续可调，自主度越高作者越少被打断

    使用示例：
      novel-agent mode -d projects/test-novel                    # 查看当前自主度
      novel-agent mode -d projects/test-novel -t auto-driver      # 全自动碰撞
      novel-agent mode -d projects/test-novel -t co-pilot         # 协同审校
      novel-agent mode -d projects/test-novel -a 60               # 自主度设为 60
    """
    from pathlib import Path

    from agent.workflows.m8_mode import (
        PRESET_AUTO_DRIVER,
        PRESET_COPILOT,
        ModeController,
    )

    project_path = Path(project_dir)
    enforce_gate(str(project_path), "mode")
    if not (project_path / ".state").exists():
        console.print(
            f"[bold red]✗[/bold red] {project_path / '.state'} 不存在，请先运行 start"
        )
        raise typer.Exit(code=1)

    controller = ModeController(project_dir=project_path, console=console)
    preset_map = {"auto-driver": PRESET_AUTO_DRIVER, "co-pilot": PRESET_COPILOT}

    # 优先处理 --autonomy 显式数值
    if autonomy is not None:
        result = controller.set_autonomy(autonomy)
        console.print(f"[bold green]✓[/bold green] {result.message}")
        controller.show_status()
        return

    if not target:
        controller.show_status()
        console.print()
        controller.show_all_modes()
        return

    try:
        if target in preset_map:
            result = controller.set_autonomy(preset_map[target])
        elif target.lstrip("-").isdigit():
            result = controller.set_autonomy(int(target))
        else:
            result = controller.switch(target)
        if result.changed:
            console.print(f"[bold green]✓[/bold green] {result.message}")
            controller.show_status()
        else:
            console.print(f"[yellow]{result.message}[/yellow]")
    except ValueError as e:
        console.print(f"[bold red]✗[/bold red] {e}")
        raise typer.Exit(code=1) from e
