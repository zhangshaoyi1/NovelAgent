"""evaluate 命令 —— 全书「不崩」体检（Phase 2）

对已有书稿跑 Evaluator 七维「不崩」套件，输出量化体检报告；
不达标时可自动回溯最近 N 章（--auto-repair 会触发重写闭环）。

也可作为独立质量门禁接入 CI / 发布前检查。
"""

from __future__ import annotations

import os
from pathlib import Path

from agent.cli._app import app, command, console, typer
from agent.cli._shared import *  # enforce_gate / emit_result / make_quiet_console
from agent.core.state_machine import State


@command(allowed_states=(State.WRITING, State.PAUSED, State.COMPLETED))
def evaluate(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    json_output: bool = typer.Option(
        False, "--json", help="以 JSON 形式输出体检报告到 stdout"
    ),
    env_file: str = typer.Option(
        None, "--env", help="指定 .env 文件（透传下游 LLMClient）"
    ),
    no_rollback: bool = typer.Option(
        False, "--no-rollback", help="仅出报告，不执行自动回溯"
    ),
    auto_repair: bool = typer.Option(
        False, "--auto-repair",
        help="不达标时自动回溯并触发重写闭环（需 LLM；会修改书稿）"
    ),
    rollback_window: int = typer.Option(
        5, "--rollback-window", help="自动回溯章数（默认 5）"
    ),
    max_rollback: int = typer.Option(
        3, "--max-rollback", help="最大回溯次数（默认 3）"
    ),
) -> None:
    """全书「不崩」体检 - 七维量化报告 + 可选自动回溯修复

    跑伏笔回收率 / 节奏异常（确定性）+ 人设/设定/连贯/追读/逻辑（LLM 评测），
    输出量化报告。默认不修改书稿；加 --auto-repair 可在不达标时回溯并重写。
    """
    if env_file:
        os.environ["NOVEL_AGENT_DOTENV"] = env_file

    project_path = Path(project_dir)
    if not (project_path / "world.md").exists():
        if json_output:
            emit_result(
                {"success": False, "error": {"code": "no_world",
                                             "message": f"{project_path / 'world.md'} 不存在"}},
                json_mode=True,
            )
        else:
            console.print(f"[bold red]✗[/bold red] {project_path / 'world.md'} 不存在")
        raise typer.Exit(code=1)

    enforce_gate(str(project_path), "evaluate", json_mode=json_output)

    workflow_console = make_quiet_console() if json_output else console
    from agent.agents.evaluator_agent import EvaluatorAgent

    evaluator = EvaluatorAgent(
        project_path,
        console=workflow_console,
        auto_rollback=not no_rollback,
        rollback_window=rollback_window,
        max_rollback_attempts=max_rollback,
    )

    try:
        if auto_repair:
            def rewriter(chapter_nums: list[int]) -> None:
                from agent.workflows.agentic_write import AgenticWriteWorkflow

                w = AgenticWriteWorkflow(project_dir=project_path, console=workflow_console)
                for _ in chapter_nums:
                    w.run()

            report = evaluator.evaluate_with_repair(rewriter)
        else:
            report = evaluator.evaluate()

        if json_output:
            emit_result({"success": True, "report": report.to_dict()}, json_mode=True)
            return
        console.print(report.to_markdown())
    except Exception as e:
        if json_output:
            emit_result({"success": False, "error": {"code": "evaluate_failed", "message": str(e)}},
                        json_mode=True)
        else:
            console.print(f"[bold red]✗ 评测失败[/bold red] {e}")
        raise typer.Exit(code=1) from e
