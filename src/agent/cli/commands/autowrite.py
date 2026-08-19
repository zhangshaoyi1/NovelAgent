"""autowrite 命令 —— 全流程自主写作入口（Phase 2）

一条命令完成：Planner 规划 → 逐章自主写作（Phase 1 WriterAgent）→ 编辑并联审查
→ 记忆回写 → Evaluator 全书「不崩」终审 + 自动回溯修复 → 输出量化报告。

这是设计文档 §1.1「用户旅程」的 CLI 落地形态：用户只需给一段思路，其余全自动。
"""

from __future__ import annotations

import os
from pathlib import Path

from agent.cli._app import app, command, console, typer
from agent.cli._shared import *  # enforce_gate / emit_result / make_quiet_console
from agent.core.state_machine import State


@command(allowed_states=(
    State.INIT, State.CONFIGURING, State.DISCUSSING, State.ARCHITECTING,
    State.ARCH_CONFIRMED, State.OUTLINING, State.CHARACTER_DESIGN, State.WRITING,
))
def autowrite(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    json_output: bool = typer.Option(
        False, "--json", help="以 JSON 形式输出结果到 stdout"
    ),
    env_file: str = typer.Option(
        None, "--env", help="指定 .env 文件（仅本次命令生效，透传下游 LLMClient）"
    ),
    brief: str = typer.Option(
        "", "--brief", help="创作思路（题材/核心梗/风格/体量）；留空则跳过规划"
    ),
    chapters: int = typer.Option(
        0, "--chapters", "-n", help="目标章节数（0 表示取 MasterPlan 或默认 100）"
    ),
    mode: str = typer.Option(
        "auto", "--mode", help="写章引擎档位：auto / heavy / light"
    ),
    no_eval: bool = typer.Option(
        False, "--no-eval", help="跳过 Evaluator 全书终审（仅写作）"
    ),
    rollback_window: int = typer.Option(
        5, "--rollback-window", help="不达标时自动回溯的章数（默认 5，可配置）"
    ),
    max_rollback: int = typer.Option(
        3, "--max-rollback", help="最大回溯次数，超过则上报人工（默认 3）"
    ),
) -> None:
    """全流程自主写作 - Planner→写作→编辑→记忆→评测+自动回溯

    默认走 **自主 Agent 流水线**：架构师规划、Writer 自主写章、主编并联审查、
    评测员跑「不崩」套件并在不达标时自动回溯最近 N 章重写，最终输出量化体检报告。

    状态转换：CHARACTER_DESIGN → WRITING（首次）／WRITING → WRITING（续写）。
    """
    if env_file:
        os.environ["NOVEL_AGENT_DOTENV"] = env_file

    project_path = Path(project_dir)
    # 零前置（仅给 brief）不再硬拒：交给 pipeline 内部编排自主生成 world.md（拍板 #6）。
    # 仅在非 JSON 模式给出一句提示，避免污染 --json 的 stdout 信封。
    if not (project_path / "world.md").exists():
        if not json_output:
            console.print(
                "[cyan]未检测到 world.md，autowrite 将自主规划生成设定集/架构/大纲/角色[/cyan]"
            )

    enforce_gate(str(project_path), "autowrite", json_mode=json_output)

    workflow_console = make_quiet_console() if json_output else console
    from agent.workflows.agentic_pipeline import AgenticPipelineWorkflow

    pipeline = AgenticPipelineWorkflow(
        project_dir=project_path,
        tier=mode if mode in ("auto", "heavy", "light") else "auto",
        brief=brief,
        target_chapters=chapters if chapters > 0 else None,
        eval_enabled=not no_eval,
        rollback_window=rollback_window,
        max_rollback_attempts=max_rollback,
        console=workflow_console,
    )

    try:
        result = pipeline.run()
        if json_output:
            emit_result({"success": True, **result.to_dict()}, json_mode=True)
            return
        console.print(
            f"\n[bold green]✓ 全流程自主写作完成[/bold green] "
            f"新写 {result.chapters_written} 章 · 末章 {result.final_chapter}"
            + (f" · 已上报人工：{result.escalated_reason}" if result.escalated else "")
        )
    except Exception as e:
        if json_output:
            emit_result({"success": False, "error": {"code": "autowrite_failed", "message": str(e)}},
                        json_mode=True)
        else:
            console.print(f"[bold red]✗ 全流程自主写作失败[/bold red] {e}")
        raise typer.Exit(code=1) from e
