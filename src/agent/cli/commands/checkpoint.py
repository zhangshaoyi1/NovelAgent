"""批次边界作者检查点（PRD A2 / 登记单 20261001_信任链与叙事上限六项能力·子项 3）。

pipeline 每批结束自动写 ``.state/checkpoint.json`` 风险摘要卡片，并按自主度
三挡决定是否挂起（HEAVY 每批停 / LIGHT 仅风险停 / AUTO 从不停）。挂起态下
``autowrite`` 被命令门禁拦截，作者通过本命令组裁决：

  checkpoint            # 查看摘要卡片（--json 供 Web/脚本消费）
  checkpoint continue   # 放行（AWAITING_CHECKPOINT → WRITING）
  checkpoint skip       # 本次放行且临时升到 AUTO——仅下一批不挂起后恢复原值

定向重写/调整计划/回滚直接用既有命令（rewrite / adjust-route / rollback，
它们在挂起态下可用），不发明新动词。
"""

from __future__ import annotations

import json

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *

from agent.core.engine.state_machine import State


def _load_card(project_dir):
    p = project_dir / ".state" / "checkpoint.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 - 卡片损坏显性降级为空
        console.print(f"[yellow]⚠ 检查点卡片不可读（{e}），按空卡片处理[/yellow]")
        return None


@command(allowed_states=(State.WRITING, State.AWAITING_CHECKPOINT, State.PAUSED))
def checkpoint(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    json_output: bool = typer.Option(
        False, "--json", help="以 JSON 形式输出卡片（供 Web/脚本消费）"
    ),
) -> None:
    """查看批次检查点摘要卡（风险异常 / 下批计划要点 / 可用动作）。"""
    from pathlib import Path

    project_path = Path(project_dir)
    card = _load_card(project_path)
    if card is None:
        msg = "暂无检查点卡片（批次结束后自动生成）"
        if json_output:
            emit_result({"success": True, "checkpoint": None, "message": msg}, json_mode=True)
        else:
            console.print(f"[dim]{msg}[/dim]")
        return
    if json_output:
        emit_result({"success": True, "checkpoint": card}, json_mode=True)
        return
    br = card.get("batch_range") or []
    console.print(f"[bold]批次检查点（第 {br[0] if br else '?'}–{br[-1] if br else '?'} 章，共 {card.get('chapters_written', 0)} 章）[/bold]")
    risks = card.get("risks") or []
    if risks:
        console.print("[yellow]风险信号：[/yellow]")
        for r in risks:
            console.print(f"  · {r}")
        if card.get("low_confidence_delivery"):
            console.print("[yellow]  ⚠ 本批为低置信交付（写时门禁失明率超标），建议批末补检/抽读[/yellow]")
    else:
        console.print("[green]本批无风险信号[/green]")
    np_ = card.get("next_plan") or {}
    if np_.get("notes"):
        console.print("[cyan]下批计划要点（批末反思）：[/cyan]")
        console.print(f"  {np_['notes']}")
        for a in (np_.get("actions") or [])[:5]:
            console.print(f"  → {a}")
    if card.get("paused"):
        console.print(f"[cyan]⏸ 已挂起：{card.get('pause_reason', '')}[/cyan]")
    console.print("[dim]可用动作：[/dim]")
    for a in card.get("actions") or []:
        console.print(f"  {a}")


@command(allowed_states=(State.AWAITING_CHECKPOINT,), writes=True)
def checkpoint_continue(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
) -> None:
    """放行检查点（AWAITING_CHECKPOINT → WRITING）；续写随后运行 autowrite。"""
    from pathlib import Path

    project_path = Path(project_dir)
    from agent.core.engine.state_machine import StateMachine, Event

    sm = StateMachine(project_path)
    sm.load()
    if sm.state is not State.AWAITING_CHECKPOINT:
        console.print(f"[yellow]当前状态为 {sm.state.value}，无需放行[/yellow]")
        return
    sm.transition(Event.RESUME)
    console.print("[green]✓ 检查点已放行，状态恢复 WRITING[/green]")
    console.print(f"[dim]继续写作：autowrite -d {project_path}[/dim]")
