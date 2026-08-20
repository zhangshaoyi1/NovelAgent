"""CLI 共享符号（从原 cli.py 拆出，供各命令模块 `from agent.cli._shared import *`）

包含顶层 import 与非命令 helper（如 _get_registry）。
"""

from __future__ import annotations
from typing import Any
import typer
from rich.panel import Panel
from rich.table import Table
from agent import __version__
from agent.cli._app import app, console
from agent.utils import make_quiet_console

def _get_registry() -> Any:
    global _SKILL_REGISTRY
    if _SKILL_REGISTRY is None:
        from agent.workflows.m15_bookworm import SkillRegistry

        _SKILL_REGISTRY = SkillRegistry()
    return _SKILL_REGISTRY


def enforce_gate(project_dir: str, command: str, *, json_mode: bool = False) -> None:
    """统一门禁（来自 command_router / StateMachine）

    在调用工作流前检查当前状态机是否允许该命令；不允许则打印可用命令并以
    退出码 2 中止（与 E3 高严重度冲突的退出码一致，便于上游统一处理）。

    设计要点：
    - 仅对 COMMAND_REGISTRY 中登记的命令生效；未注册的命令（如 inject-genre、
      load-genre、status、snapshot 等）一律放行，避免不完整门禁表误伤既有可用命令。
    - 项目尚未初始化（不存在 state.json）时放行，由命令自身的 world.md 校验负责。
    - json_mode=True 时门禁拒绝也走 emit_result 输出错误 JSON，再 raise typer.Exit(2)。

    Args:
        project_dir: 项目目录
        command: 命令名（不含前导斜杠，如 "write"）
        json_mode: 是否以 JSON 形式输出错误信封
    """
    from pathlib import Path

    from agent.core.command_router import COMMAND_REGISTRY
    from agent.core.state_machine import StateMachine

    cmd = "/" + command.replace("_", "-")
    known = {c.name for c in COMMAND_REGISTRY}
    if cmd not in known:
        return  # 辅助命令不纳入门禁

    sm = StateMachine(Path(project_dir))
    # 项目尚未初始化（不存在 state.json）时放行，由命令自身的 world.md 校验负责
    if not sm.state_file.exists():
        return
    sm.load()
    if not sm.is_command_allowed(cmd):
        if json_mode:
            emit_result(
                {
                    "success": False,
                    "error": {
                        "code": "gate_rejected",
                        "message": (
                            f"命令 {cmd} 在当前阶段 ({sm.state.value}) 不可用"
                        ),
                    },
                },
                json_mode=True,
            )
        else:
            allowed = sm.allowed_commands()
            console.print(
                f"[bold red]✗[/bold red] 命令 {cmd} 在当前阶段 "
                f"({sm.state.value}) 不可用。"
            )
            console.print(f"[dim]可用命令：{', '.join(allowed)}[/dim]")
        raise typer.Exit(code=2)


def emit_result(
    result: dict,
    *,
    rich_render: "Callable[[], None] | None" = None,
    json_mode: bool = False,
) -> None:
    """统一输出：json 模式仅把 result 以 JSON 打到 stdout；否则调用 rich_render() 渲染富文本。

    设计要点：
    - json_mode=False（默认）：调用 rich_render()，走现有 rich 表格/Panel 输出。
    - json_mode=True：仅把 result 用 json.dumps(..., ensure_ascii=False) 写到 stdout，
      完全不调用 rich_render()（配合 make_quiet_console 将工作流内部 rich 输出导向 stderr）。

    Args:
        result: 结果 dict（成功含 success/各字段；失败含 success=False + error 信封）。
        rich_render: 无参 callable，负责打印 rich 表格/Panel（json 模式不调用）。
        json_mode: 是否以 JSON 形式输出到 stdout。
    """
    if json_mode:
        import json
        import sys

        sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
    elif rich_render is not None:
        rich_render()


def print_cost_summary(cost: dict, *, json_mode: bool = False) -> None:
    """非 JSON 模式打印成本汇总（G7 拍板 4：调用次数/token/失败/延迟/基线/告警）。

    三命令（autowrite/evaluate/appeal）收尾复用；``tracked=False`` 或空字典时打印
    「本次调用未追踪」占位而非静默 0（拍板 5 / 共享知识 #7，不阻断主流程）。
    """
    if not cost or cost.get("tracked") is False:
        console.print("[dim]本次调用未追踪（仅统计已有记录）[/dim]")
        return
    console.print("\n[bold cyan]本次运行成本[/bold cyan]")
    console.print(
        f"调用 {cost.get('calls', 0)} 次 · token in {cost.get('tokens_in', 0):,}"
        f" / out {cost.get('tokens_out', 0):,} / total {cost.get('tokens_total', 0):,}"
    )
    console.print(
        f"失败 {cost.get('failures', 0)} · 平均延迟 {cost.get('avg_latency_ms', 0)} ms"
    )
    console.print(
        f"成本基线（tier/chapters）：{cost.get('baseline_low', 0)/1_000_000:.2f}M"
        f"–{cost.get('baseline_high', 0)/1_000_000:.2f}M tokens"
    )
    if cost.get("alert"):
        console.print(f"[yellow]{cost['alert']}[/yellow]")
    else:
        console.print("[green]成本在基线内[/green]")

