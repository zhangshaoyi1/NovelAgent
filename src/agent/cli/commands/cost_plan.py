"""cost_plan 命令 —— 写前成本预估（G10 P0-1，拍板 1：纯复用 CostModel.estimate_book，零新统计）。

放 cli/commands/ 与既有 cost.py 命令并列（只读看板类命令）；
`resolve_book_chapters` / `build_cost_plan` 亦被 autowrite 开写前引导复用（跨命令 import，
见设计 §12 共享知识 #9）。全 try/except 降级占位不阻断（补充边界 3）。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from agent.cli._app import app, command, console, typer
from agent.cli._shared import *  # emit_result / make_quiet_console


def _cli_value(v: Any, default: Any) -> Any:
    """归一化 CLI 参数：经 typer 真实调用时值为转换后的标量；
    直接函数调用（测试）时默认值是 typer.OptionInfo，取 .default 还原为标量。"""
    if hasattr(v, "default"):
        return v.default
    return v


def resolve_book_chapters(project_dir: str | Path, chapters: int | None = None) -> int:
    """chapters 缺省链（拍板 1：MasterPlan/state/当前章数，无则 300）。

    1) 显式 --chapters N → N；
    2) MasterPlan.total_chapters（写前目标，对齐 pipeline _resolve_target 397-407）；
    3) state.progress.total_written（当前已写章数，对齐 G9 口径）；
    4) chapters/ 章节文件数（对齐 build_cost_summary 行 157-159）；
    5) 300（兜底）。
    每步 try/except，全部离线确定性、零 LLM。
    """
    if chapters:
        return int(chapters)
    try:
        from agent.agents.planner import PlannerAgent

        plan = PlannerAgent(project_dir, llm_client=None).load_plan()
        if plan is not None and getattr(plan, "total_chapters", 0):
            return int(plan.total_chapters)
    except Exception:  # noqa: BLE001
        pass
    try:
        from agent.core.engine.state_machine import StateMachine

        sm = StateMachine(project_dir)
        sm.load()
        n = int((sm.progress or {}).get("total_written", 0) or 0)
        if n > 0:
            return n
    except Exception:  # noqa: BLE001
        pass
    try:
        from agent.core.story.chapters import list_chapter_files

        n = len(list_chapter_files(project_dir))
        if n > 0:
            return n
    except Exception:  # noqa: BLE001
        pass
    return 300


def build_cost_plan(
    project_dir: str | Path, chapters: int | None = None, model: str | None = None
) -> dict[str, Any]:
    """三档预估表（拍板 1：纯复用 CostModel.estimate_book，零新统计）。

    Args:
        project_dir: 小说项目目录。
        chapters: 目标章节数（None 走缺省链，见 resolve_book_chapters）。
        model: 估算模型名（默认 creative-strong）。

    Returns:
        {"chapters": N,
         "tiers": [{"tier","tokens_low","tokens_high","cost_low_usd","cost_high_usd"}, ...]
                  （economy/balanced/quality 顺序）,
         "guidance": "建议档位文案（含可复制命令）"}
    异常降级返回占位（chapters=0、tiers=[], guidance=""），不阻断调用方。
    """
    try:
        from agent.core.llmops.cost import CostModel

        n = resolve_book_chapters(project_dir, chapters)
        cm = CostModel()
        tiers = []
        for t in ("economy", "balanced", "quality"):
            tiers.append(cm.estimate_book(t, n, model).to_dict())
        guidance = (
            f"经济档 {n} 章预估 {tiers[0]['tokens_low']/1_000_000:.1f}M–{tiers[0]['tokens_high']/1_000_000:.1f}M tokens"
            f"；均衡档 {tiers[1]['tokens_low']/1_000_000:.1f}M–{tiers[1]['tokens_high']/1_000_000:.1f}M tokens"
            f"；质量档 {tiers[2]['tokens_low']/1_000_000:.1f}M–{tiers[2]['tokens_high']/1_000_000:.1f}M tokens。"
            f"使用 `novel-agent autowrite -d {project_dir} --cost-tier balanced` 开跑。"
        )
        return {"chapters": n, "tiers": tiers, "guidance": guidance}
    except Exception:  # noqa: BLE001 - 预估异常降级占位不阻断（补充边界 3）
        return {"chapters": 0, "tiers": [], "guidance": ""}


@command(global_=True)
def cost_plan(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    chapters: int = typer.Option(
        None, "--chapters", "-n", help="目标章节数（缺省取 MasterPlan/state/当前章数，无则 300）"
    ),
    json_output: bool = typer.Option(
        False, "--json", help="以 JSON 形式输出三档预估到 stdout"
    ),
    env_file: str = typer.Option(None, "--env", help="指定 .env 文件（透传）"),
    model: str = typer.Option(None, "--model", help="估算模型名（默认 creative-strong）"),
) -> None:
    """写前成本预估 - 三档 token/成本区间 + 档位引导

    纯复用 CostModel.estimate_book（离线确定性零 LLM）；只读，不修改书稿/状态。
    """
    _env_file = _cli_value(env_file, None)
    if _env_file:
        os.environ["NOVEL_AGENT_DOTENV"] = _env_file

    _project_dir = _cli_value(project_dir, "projects/my-novel")
    _chapters = _cli_value(chapters, None)
    _json = bool(_cli_value(json_output, False))
    _model = _cli_value(model, None)

    # G10（拍板 1）：复用统一门禁（cost-plan 为全局只读命令，正常状态一律放行）
    from agent.cli._shared import enforce_gate

    enforce_gate(str(_project_dir), "cost_plan", json_mode=_json)

    plan = build_cost_plan(_project_dir, _chapters, _cli_value(model, None))

    if _json:
        # 信封形状对齐设计 §2.1（{"success","cost_plan"}）+ B1 任务摘要
        # （{"success","chapters","tier_estimates"}）双口径，只增不冲突。
        emit_result(
            {
                "success": True,
                "cost_plan": plan,
                "chapters": plan["chapters"],
                "tier_estimates": plan["tiers"],
            },
            json_mode=True,
        )
        return

    workflow_console = make_quiet_console() if _json else console
    workflow_console.print("[bold cyan]写前成本预估[/bold cyan]")
    workflow_console.print(
        f"章节基数：{plan['chapters']} 章（缺省链：MasterPlan → state → 章节文件数 → 300）"
    )
    for row in plan["tiers"]:
        workflow_console.print(
            f"  {row['tier']:<8} tokens {row['tokens_low']/1_000_000:.1f}M–{row['tokens_high']/1_000_000:.1f}M"
            f" · 成本 ${row['cost_low_usd']:.0f}–${row['cost_high_usd']:.0f}"
        )
    workflow_console.print(f"[dim]{plan['guidance']}[/dim]")
