from __future__ import annotations

import os
from pathlib import Path

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *

from agent.core.engine.state_machine import State

@command(allowed_states=(State.WRITING,))
def adjust_relation(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    intent: str = typer.Option(
        ..., "--intent", "-i", prompt="请描述关系调整意图",
        help="调整意图，例如：赵无极对林寻从对立转为暗中赏识"
    ),
    json_output: bool = typer.Option(
        False, "--json",
        help="以 JSON 形式输出结果到 stdout（工作流内部 rich 输出转 stderr）",
    ),
    env_file: str = typer.Option(
        None, "--env",
        help="指定 .env 文件（仅本次命令生效，透传给下游 GatewayAdapter）",
    ),
) -> None:
    """M6 调整角色关系网（旧边标记为 archived，不删除）

    基于 PRD F6.2：
      1. 旧关系不会被删除，而是标记 archived + 强度=0 + 说明
      2. graph.md 的 Mermaid 只渲染活跃边，归档边追加到"归档边"章节
      3. 生成一致性影响报告，分析对角色弧线、伏笔、已写章节的影响
      4. 给出解决方案建议

    状态要求：WRITING / CHARACTER_DESIGN / PAUSED

    Args:
        project_dir: 小说项目目录
        intent: 用户的调整意图（自然语言描述）
    """
    from agent.workflows.m6_adjust import M6AdjustRelationWorkflow

    # D：--env 透传
    if env_file:
        os.environ["NOVEL_AGENT_DOTENV"] = env_file

    project_path = Path(project_dir)
    enforce_gate(str(project_path), "adjust_relation", json_mode=json_output)
    if not (project_path / "world.md").exists():
        if json_output:
            emit_result(
                {
                    "success": False,
                    "error": {
                        "code": "no_world",
                        "message": f"{project_path / 'world.md'} 不存在，请先运行 start",
                    },
                },
                json_mode=True,
            )
        else:
            console.print(
                f"[bold red]✗[/bold red] {project_path / 'world.md'} 不存在，请先运行 start"
            )
        raise typer.Exit(code=1)

    # 接线：LLM 调用事件 → <project>/.events/events.jsonl（复用公共接线，避免复制）
    from agent.core.event_sourcing.llm_wiring import wire_llm_event_hook

    wire_llm_event_hook(project_path)

    workflow_console = make_quiet_console() if json_output else console
    workflow = M6AdjustRelationWorkflow(project_dir=project_path, console=workflow_console)
    try:
        result = workflow.run(user_intent=intent)
        report = result.impact_report
        if json_output:
            sev = report.severity_count
            emit_result(
                {
                    "success": True,
                    "nodes_count": result.nodes_count,
                    "new_edges_count": result.new_edges_count,
                    "archived_edges_count": result.archived_edges_count,
                    "conflicts": {
                        "high": sev["high"],
                        "medium": sev["medium"],
                        "low": sev["low"],
                    },
                },
                json_mode=True,
            )
            return
        conflict_info = (
            f" ⚠ 冲突 高{report.severity_count['high']}"
            f"中{report.severity_count['medium']}"
            f"低{report.severity_count['low']}"
            if report.has_conflicts
            else " ✓ 无一致性冲突"
        )
        console.print(
            f"\n[bold green]✓ M6 关系调整完成[/bold green] "
            f"节点{result.nodes_count} · 新增{result.new_edges_count}条 · "
            f"归档{result.archived_edges_count}条"
            f"{conflict_info}"
        )
    except Exception as e:
        if json_output:
            emit_result(
                {
                    "success": False,
                    "error": {"code": "adjust_relation_failed", "message": str(e)},
                },
                json_mode=True,
            )
        else:
            console.print(f"\n[bold red]✗ M6 关系调整失败[/bold red] {e}")
        raise typer.Exit(code=1) from e
