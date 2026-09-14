"""payoff_plan 命令 —— 爽点剧本生成（G12 P0-1，拍板 1：确定性模板，零 LLM）。

按压力阶段（铺垫/发展/高潮/结局）确定性生成章节级爽点剧本 + 情绪目标，
写入 `.state/payoff_script.json`（用户可手编覆盖）；写章时自动注入。

用法：
    novel-agent payoff-plan -d projects/my-novel            # 生成剧本（缺省目标章数）
    novel-agent payoff-plan -d projects/my-novel -n 300     # 指定 300 章
    novel-agent payoff-plan -d projects/my-novel --json     # JSON 信封
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from agent.cli._app import app, command, console, typer
from agent.cli._shared import *  # noqa: F401,F403 - emit_result / make_quiet_console


def _cli_value(v: Any, default: Any) -> Any:
    """归一化 CLI 参数：经 typer 真实调用时值为标量；直接函数调用时还原 OptionInfo。"""
    if hasattr(v, "default"):
        return v.default
    return v


def resolve_target_chapters(project_dir: str | Path, chapters: int | None = None) -> int:
    """章节数缺省链：MasterPlan→state→章数→300。

    实现下沉到 core（`agent.core.story.payoff_script.resolve_target_chapters`），
    因业务层不得依赖 agents 层（R6）；此处 re-export 保持 CLI 与写作主链路语义一致。
    """
    from agent.core.story.payoff_script import resolve_target_chapters as _resolve

    return _resolve(project_dir, chapters)


def build_plan(project_dir: str | Path, chapters: int | None = None) -> dict[str, Any]:
    """生成爽点剧本并落盘。

    Returns:
        {"chapters": N, "script": {"chapters": [...], "generated_at"}, "file": 路径}
    """
    n = resolve_target_chapters(project_dir, chapters)
    from agent.core.story.payoff_script import build_payoff_script, save_payoff_script

    items = build_payoff_script(n)
    path = save_payoff_script(project_dir, items)
    return {"chapters": n, "script": {"chapters": items}, "file": str(path)}


@command(global_=True)
def payoff_plan(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    chapters: int = typer.Option(
        None, "--chapters", "-n", help="目标章节数（缺省取 MasterPlan/state/当前章数，无则 300）"
    ),
    json_output: bool = typer.Option(
        False, "--json", help="以 JSON 形式输出生成结果到 stdout"
    ),
    env_file: str = typer.Option(None, "--env", help="指定 .env 文件（透传）"),
) -> None:
    """爽点剧本生成 - 按压力阶段确定性生成全书爽点/情绪目标（零 LLM）

    生成 `.state/payoff_script.json`（每章爽点类型/强度 + 情绪/张力目标），
    写章自动注入【爽点剧本】【情绪目标】段；用户可手编该文件覆盖。
    """
    _env_file = _cli_value(env_file, None)
    if _env_file:
        os.environ["NOVEL_AGENT_DOTENV"] = _env_file

    _dir = _cli_value(project_dir, "projects/my-novel")
    _chapters = _cli_value(chapters, None)
    _json = bool(_cli_value(json_output, False))

    from agent.cli._shared import enforce_gate

    enforce_gate(str(_dir), "payoff_plan", json_mode=_json)

    try:
        plan = build_plan(_dir, _chapters)
    except Exception as e:  # noqa: BLE001 - 生成失败报错信封不崩
        if _json:
            emit_result({"success": False, "error": str(e)}, json_mode=True)
        else:
            console.print(f"[bold red]✗ 剧本生成失败：{e}[/bold red]")
        raise typer.Exit(code=1)

    if _json:
        emit_result({"success": True, **plan}, json_mode=True)
        return

    workflow_console = make_quiet_console() if _json else console
    workflow_console.print(
        f"[bold cyan]爽点剧本已生成[/bold cyan]：{plan['chapters']} 章 → {plan['file']}"
    )
    workflow_console.print(
        "[dim]每章含爽点类型/强度 + 情绪/张力目标；autowrite 自动注入；可手编该文件覆盖。[/dim]"
    )
