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
from agent.core.engine.state_machine import State


@command(allowed_states=(State.WRITING, State.PAUSED, State.COMPLETED))
def evaluate(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    json_output: bool = typer.Option(
        False, "--json", help="以 JSON 形式输出体检报告到 stdout"
    ),
    env_file: str = typer.Option(
        None, "--env", help="指定 .env 文件（透传下游 GatewayAdapter）"
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
    real_score: bool = typer.Option(
        True, "--real-score/--no-real-score",
        help="真 LLM 评分（B1，默认开）：人设/设定/连贯/追读/逻辑维度由 LLM 实判，"
             "替代离线满分默认；--no-real-score 强制离线（CI/测试）。"
             "LLM 不可用时自动降级为离线安全默认。"
    ),
    # ---- G7 新增：展示开关（拍板 6：默认全开，可关）----
    no_human_summary: bool = typer.Option(
        False, "--no-human-summary", help="关闭人话总结段（保留既有表格）"
    ),
    no_cost: bool = typer.Option(
        False, "--no-cost", help="关闭成本汇总输出（--json 时 cost 置 null）"
    ),
) -> None:
    """全书「不崩」体检 - 七维量化报告 + 可选自动回溯修复

    跑伏笔回收率 / 节奏异常（确定性）+ 人设/设定/连贯/追读/逻辑（**默认真 LLM 评测**），
    输出量化报告。LLM 不可用时自动降级为离线安全默认。
    默认不修改书稿；加 --auto-repair 可在不达标时回溯并重写。
    用 --no-real-score 可强制离线评测（CI/测试）。
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
    from agent.agents.evaluator import EvaluatorAgent

    # ---- G7（补充边界 3，修复 R3-3）：接线 tracer —— 复用 agent_service.py 行 80-82 模式 ----
    from agent.client.gateway_adapter import create_gateway
    from agent.core.llmops import TraceStore, TracedLLMClient, set_tracer

    set_tracer(TraceStore(project_path))
    # 接线：LLM 调用事件 → <project>/.events/events.jsonl（复用公共接线，避免复制）
    from agent.core.event_sourcing.llm_wiring import wire_llm_event_hook

    wire_llm_event_hook(project_path)
    traced_llm = TracedLLMClient(create_gateway(), model="creative-strong")

    score_fn = None
    if real_score:
        from agent.core.quality.scoring.reader_appeal import ReaderAppealScorer

        score_fn = ReaderAppealScorer(llm_client=traced_llm).score   # 改：裸 GatewayAdapter → traced_llm

    # D-J：CLI 侧（高于 workflows）构造并注入回退能力，agents 不再直接 import workflows
    from agent.workflows.evaluation.m10_rollback import M10RollbackWorkflow

    evaluator = EvaluatorAgent(
        project_path,
        console=workflow_console,
        auto_rollback=not no_rollback,
        rollback_window=rollback_window,
        max_rollback_attempts=max_rollback,
        score_fn=score_fn,
        rollback_provider=M10RollbackWorkflow(project_path, console=workflow_console),
        # G7：人话总结层展示开关（拍板 6：默认开，--no-human-summary 关闭）
        human_summary=not bool(getattr(no_human_summary, "default", no_human_summary)),
    )

    try:
        if auto_repair:
            def rewriter(chapter_nums: list[int]) -> None:
                from agent.workflows.writing.agentic_write import AgenticWriteWorkflow

                w = AgenticWriteWorkflow(project_dir=project_path, console=workflow_console)
                for _ in chapter_nums:
                    w.run()

            report = evaluator.evaluate_with_repair(rewriter)
        else:
            report = evaluator.evaluate()

        if json_output:
            # G7（拍板 4/6）：--json 信封增 cost（--no-cost 置 null；summary 已随 report.to_dict()）
            from agent.core.llmops import build_cost_summary

            payload = {"success": True, "report": report.to_dict()}
            if bool(getattr(no_cost, "default", no_cost)):
                payload["cost"] = None
            else:
                payload["cost"] = build_cost_summary(project_path, "balanced", None)
            emit_result(payload, json_mode=True)
            return
        console.print(report.to_markdown())
        # G7（拍板 4）：非 JSON 收尾成本汇总（--no-cost 跳过）
        if not bool(getattr(no_cost, "default", no_cost)):
            from agent.core.llmops import build_cost_summary

            print_cost_summary(build_cost_summary(project_path, "balanced", None))
    except Exception as e:
        if json_output:
            emit_result({"success": False, "error": {"code": "evaluate_failed", "message": str(e)}},
                        json_mode=True)
        else:
            console.print(f"[bold red]✗ 评测失败[/bold red] {e}")
        raise typer.Exit(code=1) from e
