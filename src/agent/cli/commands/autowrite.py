"""autowrite 命令 —— 全流程自主写作入口（Phase 2）

一条命令完成：Planner 规划 → 逐章自主写作（Phase 1 WriterAgent）→ 编辑并联审查
→ 记忆回写 → Evaluator 全书「不崩」终审 + 自动回溯修复 → 输出量化报告。

这是设计文档 §1.1「用户旅程」的 CLI 落地形态：用户只需给一段思路，其余全自动。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from agent.cli._app import app, command, console, typer
from agent.cli._shared import *  # enforce_gate / emit_result / make_quiet_console
from agent.core.state_machine import State
from agent.workflows.agentic_pipeline import AgenticPipelineWorkflow


def _cli_value(v: Any, default: Any) -> Any:
    """归一化 CLI 参数：经 typer 真实调用时值为转换后的标量；
    直接函数调用（测试）时默认值是 typer.OptionInfo，取 .default 还原为标量。"""
    if hasattr(v, "default"):
        return v.default
    return v


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
    max_time: int = typer.Option(
        None, "--max-time", help="整轮墙钟上限（秒，0=不限制）"
    ),
    cost_tier: str = typer.Option(
        "balanced", "--cost-tier", help="预算档位：economy / balanced / quality"
    ),
    budget_margin: float = typer.Option(
        1.0, "--budget-margin", help="预算安全系数（默认 1.0）"
    ),
    llm_timeout: int = typer.Option(
        None, "--llm-timeout", help="单调用超时（秒，覆盖 .env 的 LLM_TIMEOUT）"
    ),
    appeal_gate: bool = typer.Option(
        True, "--appeal-gate", help="开启迷爱看双闸终门禁（默认开）"
    ),
    no_appeal_gate: bool = typer.Option(
        False, "--no-appeal-gate", help="关闭迷爱看双闸终门禁"
    ),
    appeal_threshold: int = typer.Option(
        60, "--appeal-threshold", help="迷爱看综合分合格线（默认 60）"
    ),
    appeal_window: int = typer.Option(
        1, "--appeal-window", help="迷爱看评测末 N 章（默认 1，仅末章）"
    ),
    # ---- G6 新增：三闸 CLI（拍板 #6，与 --appeal-* 并列独立）----
    golden_three_gate: bool = typer.Option(
        True, "--golden-three-gate", help="开启黄金三章门禁（B4，默认开）"
    ),
    no_golden_three_gate: bool = typer.Option(
        False, "--no-golden-three-gate", help="关闭黄金三章门禁"
    ),
    golden_three_threshold: int = typer.Option(
        60, "--golden-three-threshold", help="黄金三章综合分合格线（默认 60，复用 G5 档位）"
    ),
    golden_three_floor: int = typer.Option(
        40, "--golden-three-floor", help="黄金三章单维触底线（默认 40）"
    ),
    ai_gate: bool = typer.Option(
        True, "--ai-gate", help="开启去 AI 味护栏（B5，默认开）"
    ),
    no_ai_gate: bool = typer.Option(
        False, "--no-ai-gate", help="关闭去 AI 味护栏（不注入 guardrails，行为与 G4 一致）"
    ),
    ai_gate_mode: str = typer.Option(
        "advisory", "--ai-gate-mode", help="AI 味门禁模式：advisory（标红不阻断）/ block（拒落盘）"
    ),
    ai_flavor_words: str = typer.Option(
        None, "--ai-flavor-words", help="追加 AI 味词（逗号分隔，P1）"
    ),
    padding_gate: bool = typer.Option(
        True, "--padding-gate", help="开启防注水门禁（B6，默认开）"
    ),
    no_padding_gate: bool = typer.Option(
        False, "--no-padding-gate", help="关闭防注水门禁"
    ),
    padding_threshold: float = typer.Option(
        0.30, "--padding-threshold", help="重复句占比阈值（默认 0.30）"
    ),
    # ---- G7 新增：展示开关（拍板 6：默认全开，可关）----
    no_human_summary: bool = typer.Option(
        False, "--no-human-summary", help="关闭人话总结段（保留既有表格）"
    ),
    no_cost: bool = typer.Option(
        False, "--no-cost", help="关闭成本汇总输出（--json 时 cost 置 null）"
    ),
    # ---- G8 新增：主线推进 + 结局模式开关（拍板 6：默认全开，可关）----
    mainline_window: int = typer.Option(
        5, "--mainline-window", help="支线推进决策窗口（章，默认 5，≥1）"
    ),
    ending_ratio: float = typer.Option(
        0.25, "--ending-ratio", help="结局模式触发比例（0-0.5，默认 0.25，越界钳制）"
    ),
    no_mainline_gate: bool = typer.Option(
        False, "--no-mainline-gate", help="关闭主线推进门禁（不决策不注入 mainline_progress 维度）"
    ),
    no_ending_gate: bool = typer.Option(
        False, "--no-ending-gate", help="关闭结局收敛门禁（不触发结局模式不注入 ending_convergence 维度）"
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

    # G4 进度回调（T4）：stderr 输出避免污染 JSON 信封
    def on_progress(phase: str, current: int, total: int) -> None:
        """进度回调（stderr 输出避免污染 JSON）。"""
        if json_output:
            # JSON 模式：stderr 输出进度，避免污染 stdout 信封
            sys.stderr.write(f"[progress] {phase}: {current}/{total}\n")
            return
        # 非 JSON 模式：直接 console.print
        if phase == "planning":
            console.print("[cyan]规划中...[/cyan]")
        elif phase == "writing":
            console.print(f"[dim]写章进度：{current}/{total}[/dim]")
        elif phase == "evaluating":
            console.print("[cyan]评测中...[/cyan]")

    # G6（拍板 #4/#6 + 补充边界 3）：B5 接线 —— 默认注入 build_guardrails() + gate_mode；
    # --no-ai-gate 不注入（guardrails=None，行为与 G4 一致）；--ai-flavor-words 追加词表。
    # 注意：直接函数调用（测试）时默认值为 typer.OptionInfo，先归一化为标量（_cli_value）。
    _golden_gate = bool(_cli_value(golden_three_gate, True)) and not bool(_cli_value(no_golden_three_gate, False))
    _padding_gate = bool(_cli_value(padding_gate, True)) and not bool(_cli_value(no_padding_gate, False))
    guardrails_obj = None
    gate_mode = "advisory"
    if bool(_cli_value(ai_gate, True)) and not bool(_cli_value(no_ai_gate, False)):
        from agent.core.guardrails import build_guardrails

        gr = build_guardrails()
        if _cli_value(ai_flavor_words, None):
            gr.ai_flavor_words.extend(
                w.strip() for w in _cli_value(ai_flavor_words, "").split(",") if w.strip()
            )
        guardrails_obj = gr
        gate_mode = _cli_value(ai_gate_mode, "advisory")
        gate_mode = gate_mode if gate_mode in ("advisory", "block") else "advisory"

    pipeline = AgenticPipelineWorkflow(
        project_dir=project_path,
        tier=mode if mode in ("auto", "heavy", "light") else "auto",
        brief=brief,
        target_chapters=chapters if chapters > 0 else None,
        eval_enabled=not no_eval,
        rollback_window=rollback_window,
        max_rollback_attempts=max_rollback,
        console=workflow_console,
        # G4 新增参数（T4）：透传到 pipeline
        max_time=max_time if max_time and max_time > 0 else None,
        cost_tier=cost_tier,
        budget_margin=budget_margin,
        llm_timeout=llm_timeout,
        on_progress=on_progress,
        # G5：迷爱看六维双闸透传
        appeal_gate=appeal_gate and not no_appeal_gate,
        appeal_threshold=appeal_threshold,
        appeal_window=appeal_window,
        # ---- G6 新增：B5 接线 + B4/B6 三闸透传 ----
        guardrails=guardrails_obj,                 # 修复 B5-2 空白（此前恒 None）
        gate_mode=gate_mode,
        golden_three_gate=_golden_gate,
        golden_three_threshold=int(_cli_value(golden_three_threshold, 60)),
        golden_three_floor=int(_cli_value(golden_three_floor, 40)),
        padding_gate=_padding_gate,
        padding_threshold=float(_cli_value(padding_threshold, 0.30)),
        # ---- G7 新增：人话总结层展示开关（拍板 6：默认开，--no-human-summary 关闭）----
        human_summary=not bool(_cli_value(no_human_summary, False)),
        # ---- G8 新增：主线推进 + 结局模式（钳制语义：window≥1，ratio∈[0,0.5]）----
        mainline_window=max(1, int(_cli_value(mainline_window, 5))),
        ending_ratio=max(0.0, min(0.5, float(_cli_value(ending_ratio, 0.25)))),
        mainline_gate=not bool(_cli_value(no_mainline_gate, False)),
        ending_gate=not bool(_cli_value(no_ending_gate, False)),
    )

    try:
        result = pipeline.run()

        # G4 判断是否成功（T5）：含 blocked/tripped/escalated
        success = not (result.blocked or result.tripped or result.escalated)

        if json_output:
            # G7（拍板 6）：--no-cost 时把 cost 置 null（emit 调用本身不改，to_dict 已含 cost）
            if bool(_cli_value(no_cost, False)):
                result.cost = None
            # ---- G8（拍板 6）：--json 只增 mainline/ending 字段；关闭后置 null ----
            if bool(_cli_value(no_mainline_gate, False)):
                result.mainline = None
            if bool(_cli_value(no_ending_gate, False)):
                result.ending = None
            emit_result({"success": success, **result.to_dict()}, json_mode=True)
            return

        if success:
            console.print(
                f"\n[bold green]✓ 全流程自主写作完成[/bold green] "
                f"新写 {result.chapters_written} 章 · 末章 {result.final_chapter}"
                + (f" · 已上报人工：{result.escalated_reason}" if result.escalated else "")
            )
        else:
            # 打印失败原因（T5）
            if result.tripped:
                console.print(f"[bold red]✗ 熔断中止：{result.block_reason}[/bold red]")
            elif result.blocked:
                console.print(f"[bold red]✗ 阻塞失败：{result.block_reason}[/bold red]")
            elif result.escalated:
                console.print(f"[bold yellow]⚠ 上报人工：{result.escalated_reason}[/bold yellow]")

        # G7（拍板 4）：非 JSON 收尾成本汇总（--no-cost 跳过）
        if not bool(_cli_value(no_cost, False)):
            print_cost_summary(result.cost)

        # 退出码映射：blocked/tripped/escalated 非 0（T5）
        if not success:
            exit_code = 1 if (result.blocked or result.tripped) else 2
            raise typer.Exit(code=exit_code)

    except typer.Exit:
        raise  # 不吞 typer.Exit，保留设计退出码（blocked/tripped→1，escalated→2）
    except Exception as e:
        if json_output:
            emit_result(
                {"success": False, "error": {"code": "autowrite_failed", "message": str(e)}},
                json_mode=True
            )
        else:
            console.print(f"[bold red]✗ 全流程自主写作失败[/bold red] {e}")
        raise typer.Exit(code=1) from e