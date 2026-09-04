from __future__ import annotations

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *

@command(global_=True)
def audit_setting(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    setting: str = typer.Option(
        ..., "--setting", "-s", help="新设定文本（用引号包裹）"
    ),
    subline: str = typer.Option(
        "", "--subline", help="指定支线 ID（默认取第一个支线）"
    ),
) -> None:
    """M12 设定冲突仲裁 - 检测新设定与现有设定集的冲突

    F12.1：用户输入新设定，Agent 检测与 world.md / subline.md / character.md 的冲突，
    输出一致性影响报告。高严重度冲突需要用户仲裁。

    使用示例：
      novel-agent audit-setting -d projects/my-novel -s "主角境界提升到金丹期"
      novel-agent audit-setting -d projects/my-novel -s "新增角色：黑袍人" --subline S02
    """
    from pathlib import Path

    from agent.core.quality.consistency.conflict_service import ConflictArbiter
    from agent.client.gateway_adapter import create_gateway

    project_path = Path(project_dir)
    enforce_gate(str(project_path), "audit_setting")
    if not (project_path / "world.md").exists():
        console.print(f"[bold red]✗[/bold red] 项目未初始化（缺少 world.md）")
        raise typer.Exit(code=1)

    # 接线：LLM 调用事件 → <project>/.events/events.jsonl（复用公共接线，避免复制）
    from agent.core.event_sourcing.llm_wiring import wire_llm_event_hook

    wire_llm_event_hook(project_path)

    arbiter = ConflictArbiter(project_path, llm=create_gateway(), console=console)
    subline_id = subline if subline else None
    try:
        report = arbiter.check_new_setting(setting, subline_id=subline_id)
    except Exception as e:
        console.print(f"[bold red]✗ 冲突检测失败[/bold red] {e}")
        raise typer.Exit(code=1) from e

    arbiter.show_report(report)
    if report.needs_arbitration:
        raise typer.Exit(code=2)
