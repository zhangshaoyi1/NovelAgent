from __future__ import annotations

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *

from agent.core.engine.state_machine import State

@command(allowed_states=(State.OUTLINING,))
def design_characters(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    feedback: str = typer.Option(
        "", "--feedback", "-f", help="作者修改意见（非空则基于现有角色产物迭代修订）"
    ),
) -> None:
    """M4 角色设计 - 主角路线 + 角色档案 + 关系网 + 伏笔表 + 金手指登记

    基于已确认架构 + 已生成大纲，产出：
      - protagonist_route.md（树状成长路线）
      - characters/<姓名>.md（按 PRD 5.2 模板）
      - relations/graph.md（Mermaid 关系网）
      - foreshadows.md（初始伏笔表）
      - golden_finger_registration.md（金手指登记，冻结）

    架构未确认或未生成大纲时拒绝执行。
    带 --feedback 时按作者意见在现有角色设计基础上迭代修订。

    Args:
        project_dir: 小说项目目录
        feedback: 作者修改意见
    """
    from pathlib import Path

    from agent.workflows.m4_character import M4CharacterWorkflow

    project_path = Path(project_dir)
    # 门禁：角色设计须在大纲后阶段；带 --feedback 的迭代修订对任意状态放行
    #（由 workflow 校验前置文件已存在），满足「任何阶段都能按意见修改角色设计」。
    if not feedback:
        enforce_gate(str(project_path), "design_characters")
    if not (project_path / "world.md").exists() and not feedback:
        console.print(
            f"[bold red]✗[/bold red] {project_path / 'world.md'} 不存在，请先运行 start"
        )
        raise typer.Exit(code=1)

    workflow = M4CharacterWorkflow(project_dir=project_path)
    # 失败自动重试一次：LLM 偶发输出截断/非 JSON 导致失败时，重跑常可恢复
    #（生成类写操作失败仍响亮报错，绝不静默写残缺产物）。
    for attempt in (1, 2):
        try:
            result = workflow.run(feedback=feedback)
            console.print(
                f"\n[bold green]✓ M4 完成[/bold green] "
                f"{len(result.characters)} 名角色 / {len(result.foreshadows)} 条伏笔 / "
                f"{len(result.golden_finger_registration.get('growth_stages', []) or [])} 金手指阶段"
                + ("（按意见修订）" if feedback else "")
            )
            return
        except Exception as e:
            if attempt == 1:
                console.print(
                    f"[yellow]⚠ M4 首次失败（{e}），自动重试一次...[/yellow]"
                )
                continue
            console.print(f"\n[bold red]✗ M4 失败[/bold red] {e}")
            raise typer.Exit(code=1) from e
