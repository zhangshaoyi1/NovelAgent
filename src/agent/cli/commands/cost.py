"""cost 命令 —— LLMOps 成本 / 追踪 / 评测看板（Phase 3）

展示本项目的 LLM 调用追踪汇总、成本基线估算（§1.4 档位策略）、评测回归次数与
提示版本。纯只读看板，不修改书稿；可作为发布前成本核算与回归巡检入口。
"""

from __future__ import annotations

import os
from pathlib import Path

from agent.cli._app import app, command, console, typer
from agent.cli._shared import *  # emit_result / make_quiet_console


@command(global_=True)
def cost(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    json_output: bool = typer.Option(
        False, "--json", help="以 JSON 形式输出看板到 stdout"
    ),
    env_file: str = typer.Option(None, "--env", help="指定 .env 文件（透传）"),
    tier: str = typer.Option(
        "balanced", "--tier", help="成本基线档位：economy / balanced / quality"
    ),
    chapters: int = typer.Option(
        300, "--chapters", "-n", help="成本基线投影的章节数"
    ),
) -> None:
    """LLMOps 看板 - 调用追踪 / 成本基线 / 评测回归汇总

    读取本项目 ``.state/llmops/`` 的追踪与评测记录，给出 token 消耗、按用途分布、
    成本基线告警（超出档位上限时提示）与评测回归次数。
    """
    if env_file:
        os.environ["NOVEL_AGENT_DOTENV"] = env_file

    workflow_console = make_quiet_console() if json_output else console
    from agent.core.llmops import CostModel, EvalHarness, TraceStore

    proj = Path(project_dir)
    trace = TraceStore(proj)
    cost_model = CostModel()
    harness = EvalHarness(proj)

    totals = trace.totals()
    by_use = trace.by_use()
    baseline = cost_model.estimate_book(tier, chapters)
    alert = cost_model.alert_if_over(totals["tokens_total"], tier, chapters)

    summary = {
        "trace_totals": totals,
        "trace_by_use": by_use,
        "cost_baseline": baseline.to_dict(),
        "cost_alert": alert,
        "eval_runs": len(harness.history()),
        "regression_issues": [r.to_dict() for r in harness.detect_regression()],
    }

    if json_output:
        emit_result({"success": True, "dashboard": summary}, json_mode=True)
        return

    workflow_console.print("[bold cyan]LLMOps 看板[/bold cyan]")
    workflow_console.print(
        f"调用次数：{totals['calls']}　token：{totals['tokens_total']:,}（in {totals['tokens_in']:,} / out {totals['tokens_out']:,}）"
    )
    workflow_console.print(
        f"失败：{totals['failures']}　平均延迟：{totals['avg_latency_ms']} ms　已耗成本估算：${totals['cost']:.2f}"
    )
    workflow_console.print(
        f"成本基线（{tier} / {chapters} 章）：${baseline.cost_low_usd:.0f}–${baseline.cost_high_usd:.0f}"
    )
    if alert:
        workflow_console.print(f"[yellow]{alert}[/yellow]")
    else:
        workflow_console.print("[green]成本在基线内[/green]")
    workflow_console.print(f"评测回归记录：{len(harness.history())} 次")
    issues = harness.detect_regression()
    if issues:
        workflow_console.print("[red]检测到回归：[/red]")
        for i in issues:
            workflow_console.print(f"  - {i.message}")
    else:
        workflow_console.print("[green]未检测到回归[/green]")
