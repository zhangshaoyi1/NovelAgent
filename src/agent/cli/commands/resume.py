from __future__ import annotations

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *

from agent.core.engine.state_machine import State

@command(allowed_states=(State.PAUSED,))
def resume(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    save: bool = typer.Option(
        False, "--save", "-s", help="同时保存简报到 resume_brief.md"
    ),
) -> None:
    """M10 续作恢复 - 生成续作简报

    F10.2：长时间未操作后回来，主动输出续作简报：
      - 上次写到哪（章节/支线/时间）
      - 3 条悬而未决的剧情线
      - 未回收伏笔清单（按优先级）
      - 关系网最近变化
      - 建议下一步

    使用示例：
      novel-agent resume -d projects/my-novel
      novel-agent resume -d projects/my-novel --save
    """
    from pathlib import Path

    from agent.workflows.evaluation.m10_rollback import M10ResumeWorkflow

    project_path = Path(project_dir)
    enforce_gate(str(project_path), "resume")
    if not (project_path / ".state" / "state.json").exists():
        console.print(f"[bold red]✗[/bold red] 状态文件不存在，项目未初始化")
        raise typer.Exit(code=1)

    wf = M10ResumeWorkflow(project_path, console=console)
    try:
        brief = wf.generate_brief()
    except Exception as e:
        console.print(f"[bold red]✗ 生成简报失败[/bold red] {e}")
        raise typer.Exit(code=1) from e

    wf.show_brief(brief)

    if save:
        brief_file = project_path / "resume_brief.md"
        brief_file.write_text(brief.to_markdown(), encoding="utf-8")
        console.print(f"\n[dim]简报已保存到 {brief_file.name}[/dim]")
