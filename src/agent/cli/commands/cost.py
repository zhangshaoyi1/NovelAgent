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
    from agent.core.llmops.trace import DEDUPE_WINDOW_S

    proj = Path(project_dir)
    trace = TraceStore(proj)
    cost_model = CostModel()
    harness = EvalHarness(proj)

    totals = trace.totals()
    by_use = trace.by_use()
    # 2026-09-20：跨 provider 判据抽检 / P0-1 前缀缓存分析的分组维度
    by_provider = trace.by_provider()
    by_model = trace.by_model()
    # trace 完整性：双记 ⇒ totals 被虚增 ⇒ 熔断阈值成了"摧毁扳机"（纪律 #16）
    dup_pairs = trace.duplicate_pairs()
    baseline = cost_model.estimate_book(tier, chapters)
    alert = cost_model.alert_if_over(totals["tokens_total"], tier, chapters)

    summary = {
        "trace_totals": totals,
        "trace_by_use": by_use,
        "trace_by_provider": by_provider,
        "trace_by_model": by_model,
        "trace_integrity": {
            "duplicate_pairs": len(dup_pairs),
            "window_s": DEDUPE_WINDOW_S,
            "trustworthy": len(dup_pairs) == 0,
        },
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
    # trace 完整性（先于读数呈现——读数不可信时不让人先看到数字）
    if dup_pairs:
        workflow_console.print(
            f"[yellow]⚠ trace 完整性：{len(dup_pairs)} 对重复 span（窗口 "
            f"{DEDUPE_WINDOW_S}s）⇒ totals 被虚增，熔断读数不可信[/yellow]"
        )
    else:
        workflow_console.print("[green]trace 完整性：无重复 span[/green]")
    # 分维度（2026-09-20）：provider / model
    for label, group in (("provider", by_provider), ("model", by_model)):
        if len(group) > 1 or "<unknown>" in group:
            workflow_console.print(f"[bold]按 {label}：[/bold]")
            for key, v in sorted(group.items(), key=lambda x: -x[1]["tokens_total"]):
                flag = "　[yellow](缺 provider ⇒ 补记路径)[/yellow]" if key == "<unknown>" else ""
                workflow_console.print(
                    f"  {key}：{v['calls']} 次 · {v['tokens_total']:,} token"
                    f"（失败 {v['failures']}）{flag}"
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
