from __future__ import annotations

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *

from agent.core.engine.state_machine import State

@command(allowed_states=(State.ARCH_CONFIRMED, State.OUTLINING))
def outline(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    feedback: str = typer.Option(
        "", "--feedback", "-f", help="作者修改意见（非空则基于现有大纲迭代修订）"
    ),
) -> None:
    """M3 大纲生成 - 故事简介 + 顶层支线任务列表

    支持在 OUTLINING 状态重跑（此前生成降级/截断时重新生成大纲）。
    基于已确认架构生成 outline.md，并为每条支线创建 subline.md。
    带 --feedback 时按作者意见在现有大纲基础上迭代修订（不改变状态）。
    架构未确认时拒绝执行（F14 门禁）。

    Args:
        project_dir: 小说项目目录
        feedback: 作者修改意见
    """
    from pathlib import Path

    from agent.workflows.m3_outline import M3OutlineWorkflow

    project_path = Path(project_dir)
    # 门禁：生成大纲须在架构确认后阶段；带 --feedback 的迭代修订对任意状态放行
    #（由 workflow 校验前置文件已存在），满足「任何阶段都能按意见修改大纲」。
    if not feedback:
        enforce_gate(str(project_path), "outline")
    if not (project_path / "world.md").exists() and not feedback:
        console.print(
            f"[bold red]✗[/bold red] {project_path / 'world.md'} 不存在，请先运行 start"
        )
        raise typer.Exit(code=1)

    workflow = M3OutlineWorkflow(project_dir=project_path)
    # 失败自动重试一次：LLM 偶发输出截断/非 JSON 导致失败时，重跑常可恢复
    #（生成类写操作失败仍响亮报错，绝不静默写残缺产物）。
    for attempt in (1, 2):
        try:
            result = workflow.run(feedback=feedback)
            console.print(
                f"\n[bold green]✓ M3 完成[/bold green] "
                f"共 {len(result.sublines)} 条顶层支线"
                + ("（按意见修订）" if feedback else "")
            )
            return
        except Exception as e:
            if attempt == 1:
                console.print(
                    f"[yellow]⚠ M3 首次失败（{e}），自动重试一次...[/yellow]"
                )
                continue
            console.print(f"\n[bold red]✗ M3 失败[/bold red] {e}")
            raise typer.Exit(code=1) from e
