"""主线/支线预算计划命令：mainline init（写入比例/分账）与 mainline show（查看）"""

from __future__ import annotations

import json as _json
from pathlib import Path
from typing import List, Optional

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *

from agent.core.engine.state_machine import StateMachine


@command(global_=True)
def mainline_init(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    ratio: Optional[str] = typer.Option(
        None,
        "--ratio",
        "-r",
        help="前期/中期/后期 比例（如 '40:35:25'）。仅作为意图元数据写入；"
        "分线预算用 --subline 精确指定，否则按体量均衡分账。",
    ),
    subline: Optional[List[str]] = typer.Option(
        None,
        "--subline",
        "-s",
        help="分线预算，可重复：--subline S01=240 （S0x 为支线 ID，值为章数上限）",
    ),
    horizon: Optional[int] = typer.Option(
        None, "--horizon", "-H", help="全书目标章数（覆盖体量估算；慎用）"
    ),
    json_output: bool = typer.Option(
        False, "--json", help="以 JSON 形式输出结果到 stdout"
    ),
) -> None:
    """初始化/更新主线预算计划（.state/mainline.json）

    推进裁决（MainlineOrchestrator）会把每个支线的章数上限当作硬预算 cap：
    - 越界即切下一支线（比例/预算兜底，避免某个支线无限拖长）。
    - 不 `init` 也生效：写章时按体量均衡分账自动生成。
    """
    project_path = Path(project_dir)
    enforce_gate(str(project_path), "mainline_init", json_mode=json_output)

    from agent.workflows.pipeline.mainline_orchestrator import MainlineOrchestrator

    orch = MainlineOrchestrator(project_path, StateMachine(project_path))
    plan = dict(orch.load_plan())  # load_plan 缺失时会按体量均衡分账自动生成默认计划

    if horizon is not None:
        plan["horizon_chapters"] = int(horizon)

    if ratio:
        parts = [p.strip() for p in ratio.split(":")]
        if len(parts) < 2 or not all(p.isdigit() for p in parts):
            if json_output:
                emit_result(
                    {"success": False, "error": {"code": "bad_ratio", "message": ratio}},
                    json_mode=True,
                )
            else:
                console.print(f"[red]✗[/red] --ratio 需形如 '40:35:25'，收到：{ratio}")
            raise typer.Exit(code=2)
        plan.setdefault("phase_ratio", {})
        for i, v in enumerate(parts, start=1):
            plan["phase_ratio"].setdefault(f"stage{i}", int(v))

    share = dict(plan.get("subline_share", {}) or {})
    if subline:
        for pair in subline:
            if "=" not in pair:
                continue
            sid, _, n = pair.partition("=")
            sid, n = sid.strip(), n.strip()
            if n.isdigit():
                share[sid] = int(n)
        plan["subline_share"] = share

    orch.save_plan(plan)

    if json_output:
        emit_result(
            {"success": True, "plan": plan},
            json_mode=True,
        )
        return
    console.print("[green]✓ 已写入主线预算计划[/green]")
    _render_plan(plan)


@command(global_=True)
def mainline_show(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    json_output: bool = typer.Option(
        False, "--json", help="以 JSON 形式输出结果到 stdout"
    ),
) -> None:
    """查看主线预算计划与当前推进状态"""
    project_path = Path(project_dir)
    enforce_gate(str(project_path), "mainline_show", json_mode=json_output)

    from agent.workflows.pipeline.mainline_orchestrator import MainlineOrchestrator

    orch = MainlineOrchestrator(project_path, StateMachine(project_path))
    try:
        plan = dict(orch.load_plan())
    except Exception:  # noqa: BLE001 - 计划损坏降级为空
        plan = {}

    sm = StateMachine(project_path)
    try:
        sm.load()
        progress = dict(sm.progress or {})
    except Exception:  # noqa: BLE001
        progress = {}

    result = {
        "success": True,
        "plan": plan,
        "progress": {
            "current_subline": progress.get("current_subline"),
            "total_written": progress.get("total_written"),
            "mainline_visited": progress.get("mainline_visited"),
        },
    }
    if json_output:
        emit_result(result, json_mode=True)
        return

    console.print("[bold]主线预算计划[/bold]")
    _render_plan(plan)
    console.print(f"\n[bold]当前进度[/bold]：支线 {progress.get('current_subline')} · "
                  f"已写 {progress.get('total_written')} 章 · "
                  f"已访问 {progress.get('mainline_visited')}")


def _render_plan(plan: dict) -> None:
    horizon = plan.get("horizon_chapters")
    console.print(f"  全书目标章数：{horizon if horizon is not None else '（未知）'}")
    ratio = plan.get("phase_ratio") or {}
    if ratio:
        console.print("  阶段比例意图：" + " · ".join(f"{k}={v}%" for k, v in ratio.items()))
    else:
        console.print("  阶段比例意图：（未设置，可 mainline init --ratio 40:35:25）")
    share = plan.get("subline_share") or {}
    if share:
        table = Table(title="分线章数预算（硬上界）")
        table.add_column("支线", style="cyan")
        table.add_column("预算上限(章)", justify="right")
        for sid in sorted(share):
            table.add_row(sid, str(share[sid]))
        console.print(table)
    else:
        console.print("  分线预算：（无，将按体量均衡分账自动生成）")