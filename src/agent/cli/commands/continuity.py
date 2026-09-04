from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Optional

from agent.cli._app import app, console, typer, command
from agent.cli._shared import emit_result
from agent.core.continuity import (
    ContinuityLedgerStore,
    project,
    project_to_text,
)
from agent.core.story import ForesightThread, ForesightStore, derive_status


@command(global_=True)
def continuity(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    action: str = typer.Option(
        "show", "--action",
        help="动作：show（只读展示账本 / 伏笔状态机）/ import-foreshadow（从 M13 表格导入）",
    ),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 形式输出到 stdout"),
) -> None:
    """连续性账本（G15 P0-1/P0-2 只读 + 导入）

    只读查看结构化账本（事实/信息差/未闭环/章交接）与伏笔确定性状态机；
    或 ``import-foreshadow`` 把 M13 扁平表格一行行导入 thread+beats（只读适配层）。
    """
    project_path = Path(project_dir)
    ledger = ContinuityLedgerStore(project_path)
    ledger.load()

    if action == "show":
        text = project_to_text(project(ledger))
        store = ForesightStore(project_path)
        threads = store.load()
        if json_output:
            emit_result({
                "success": True,
                "ledger": {
                    "facts": [asdict(f) for f in ledger.ledger.facts],
                    "knowledge": [asdict(k) for k in ledger.ledger.knowledge],
                    "open_loops": [asdict(lo) for lo in ledger.ledger.open_loops],
                    "handoffs": [asdict(h) for h in ledger.ledger.handoffs],
                },
                "foresight": [t.model_dump(mode="json") for t in threads],
            }, json_mode=True)
            return
        console.print(f"[bold]连续性账本（{len(ledger.ledger.facts)} 事实 / "
                      f"{len(ledger.ledger.open_loops)} 未闭环）[/bold]")
        if text:
            console.print(text)
        if threads:
            console.print("\n[bold]伏笔状态机[/bold]")
            for t in threads:
                console.print(f"  {t.fid} [{t.status}] {t.core_question} "
                              f"（{len(t.beats)} beats）")
        else:
            console.print(f"[dim].state/foresight.json 暂无伏笔线程[/dim]")
        return

    if action == "import-foreshadow":
        from agent.workflows.evaluation.m13_foreshadow import M13ForeshadowWorkflow

        wf = M13ForeshadowWorkflow(project_dir=project_path)
        flat = wf.load_foreshadows()
        if not flat:
            msg = "foreshadows.md 未找到或为空，无可导入伏笔"
            if json_output:
                emit_result({"success": False, "error": {"code": "no_foreshadow", "message": msg}}, json_mode=True)
            else:
                console.print(f"[bold red]✗[/bold red] {msg}")
            raise typer.Exit(code=1) from None

        store = ForesightStore(project_path)
        imported = 0
        for f in flat:
            beats = []
            if f.state in ("已埋", "已回收"):
                beats = [{"beat_id": f"{f.fid}-plant", "type": "plant", "exec_status": "committed", "commit_id": str(f.planted_at)}]
            if f.state == "已回收":
                beats.append({"beat_id": f"{f.fid}-payoff", "type": "payoff", "exec_status": "committed", "commit_id": str(f.planted_at)})
            thread = ForesightThread(
                fid=f.fid,
                core_question=f.content,
                hidden_truth=f.content,
                expected_resolve=f.expected_resolve,
                beats=beats,
            )
            store.upsert(thread)
            imported += 1

        if json_output:
            emit_result({"success": True, "imported": imported,
                         "threads": [t.model_dump(mode="json") for t in store.load()]}, json_mode=True)
        else:
            console.print(f"[green]✓[/green] 已导入 {imported} 条伏笔线程（含 derive_status 推导）")
        return

    if action == "repair":
        # 只读适配纠偏：重载账本 + 洗白伏笔 derive_status（纯函数重算并回写）。
        # 用于被手改过 / 版本升级后 status 与 beats 不一致的恢复。
        j = {"facts": len(ledger.ledger.facts),
             "knowledge": len(ledger.ledger.knowledge),
             "open_loops": len(ledger.ledger.open_loops),
             "handoffs": len(ledger.ledger.handoffs)}
        store = ForesightStore(project_path)
        threads = store.load()
        derive_status(threads)
        store.save(threads)
        if json_output:
            emit_result({"success": True, "ledger": j,
                         "threads": [t.model_dump(mode="json") for t in threads]}, json_mode=True)
        else:
            console.print(f"[green]✓[/green] 纠偏完成：账本 {j}；伏笔状态机 {len(threads)} 条已重新推导")
        return

    msg = f"未知动作 {action!r}，可选：show / import-foreshadow / repair"
    if json_output:
        emit_result({"success": False, "error": {"code": "bad_action", "message": msg}}, json_mode=True)
    else:
        console.print(f"[bold red]✗[/bold red] {msg}")
    raise typer.Exit(code=1) from None