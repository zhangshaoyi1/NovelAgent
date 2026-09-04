"""reader_feedback 命令 —— 读者数据回流（G12 P0-3，拍板 5：B10-lite 只影响写作）。

把读者反馈（评分/弃书点/评论）写入既有 ``pacing_store`` 追读力账本
（kind=``reader_feedback``，结构零改动），弃书点之后的章节写章时注入
【读者反馈】强化信号（章末钩子/爽点密度）。

用法：
    novel-agent reader-feedback -d proj --score 7
    novel-agent reader-feedback -d proj --abandon-at 12 --comment "第12章节奏拖"
    novel-agent reader-feedback -d proj --list          # 查看既有反馈
    novel-agent reader-feedback -d proj --json
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from agent.cli._app import app, command, console, typer
from agent.cli._shared import *  # noqa: F401,F403 - emit_result / make_quiet_console


def _cli_value(v: Any, default: Any) -> Any:
    """归一化 CLI 参数：经 typer 真实调用时值为标量；直接函数调用时还原 OptionInfo。"""
    if hasattr(v, "default"):
        return v.default
    return v


def list_feedback(project_dir: str | Path) -> list[dict[str, Any]]:
    """列出全部 reader_feedback 类债务。"""
    try:
        from agent.core.story.pacing_store import PacingStore

        return [
            {"id": d.id, "desc": d.desc, "planted_ch": d.planted_ch}
            for d in PacingStore(project_dir).get_open_debts(n=100)
            if d.kind == "reader_feedback"
        ]
    except Exception:  # noqa: BLE001 - 账本读取失败降级为空
        return []


def add_feedback(
    project_dir: str | Path,
    score: int | None = None,
    abandon_at: int | None = None,
    comment: str | None = None,
) -> dict[str, Any]:
    """写入一条读者反馈（至少一个有效参数，否则抛 ValueError）。"""
    if score is not None and (int(score) < 0 or int(score) > 10):
        raise ValueError("--score 必须在 0-10 之间")
    if abandon_at is not None and int(abandon_at) <= 0:
        raise ValueError("--abandon-at 必须为正整数")
    parts = []
    if score is not None:
        parts.append(f"读者评分 {int(score)}/10")
    if abandon_at is not None:
        parts.append(f"第{int(abandon_at)}章弃读")
    if comment:
        parts.append(comment.strip())
    if not parts:
        raise ValueError("至少提供 --score / --abandon-at / --comment 之一")

    from agent.core.story.pacing_store import Debt, PacingStore

    store = PacingStore(project_dir)
    debt = Debt(
        id=f"fb-{int(time.time() * 1000)}",
        desc="；".join(parts),
        kind="reader_feedback",
        planted_ch=int(abandon_at or 0),
    )
    store.add_debt(debt)
    return {"debt_id": debt.id, "desc": debt.desc, "planted_ch": debt.planted_ch}


@command(global_=True)
def reader_feedback(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    score: int = typer.Option(None, "--score", help="读者评分（0-10）"),
    abandon_at: int = typer.Option(None, "--abandon-at", help="弃书点章节号"),
    comment: str = typer.Option(None, "--comment", help="读者评论/反馈文本"),
    list_only: bool = typer.Option(False, "--list", help="列出既有读者反馈"),
    json_output: bool = typer.Option(
        False, "--json", help="以 JSON 形式输出到 stdout"
    ),
    env_file: str = typer.Option(None, "--env", help="指定 .env 文件（透传）"),
) -> None:
    """读者数据回流 - 记录评分/弃书点/评论，注入后续写章强化信号

    写入追读力账本（kind=reader_feedback），弃书点之后的章节写章时自动注入
    【读者反馈】段（强化章末钩子与爽点密度）。--list 可审计。
    """
    _env_file = _cli_value(env_file, None)
    if _env_file:
        os.environ["NOVEL_AGENT_DOTENV"] = _env_file

    _dir = _cli_value(project_dir, "projects/my-novel")
    _score = _cli_value(score, None)
    _abandon = _cli_value(abandon_at, None)
    _comment = _cli_value(comment, None)
    _list = bool(_cli_value(list_only, False))
    _json = bool(_cli_value(json_output, False))

    from agent.cli._shared import enforce_gate

    enforce_gate(str(_dir), "reader_feedback", json_mode=_json)

    if _list:
        items = list_feedback(_dir)
        if _json:
            emit_result({"success": True, "feedback": items}, json_mode=True)
            return
        workflow_console = make_quiet_console() if _json else console
        if not items:
            workflow_console.print("[dim]暂无读者反馈[/dim]")
            return
        workflow_console.print("[bold cyan]既有读者反馈[/bold cyan]")
        for it in items:
            workflow_console.print(
                f"  · {it['id']}｜{it['desc']}"
                + (f"（第{it['planted_ch']}章）" if it.get("planted_ch") else "")
            )
        return

    try:
        result = add_feedback(_dir, _score, _abandon, _comment)
    except ValueError as e:
        if _json:
            emit_result({"success": False, "error": str(e)}, json_mode=True)
        else:
            console.print(f"[bold red]✗ 反馈写入失败：{e}[/bold red]")
        raise typer.Exit(code=1)

    if _json:
        emit_result({"success": True, **result}, json_mode=True)
        return

    workflow_console = make_quiet_console() if _json else console
    workflow_console.print(
        f"[bold green]✓ 读者反馈已写入[/bold green]：{result['desc']}（{result['debt_id']}）"
    )
    workflow_console.print("[dim]弃书点之后的章节写章将自动强化钩子/爽点；--list 可查。[/dim]")
