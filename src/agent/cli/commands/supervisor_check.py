"""supervisor-check 命令（M26 长篇监督 · 手动/批次口）

policy.py 把 Supervisor 列在 review 层（批次/手动），但 2026-09-19 之前
``SupervisorEngine`` 全仓零调用点（假把关者）：4 个内置 checker 从未运行、
Web /quality 页却声称「事件驱动」。本命令是手动消费口；自动消费口为
autowrite 批末（``AgenticPipelineWorkflow._run_supervisor_batch_end``）。

只读：绝不修改任何项目文件；纯规则、零 LLM。
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.table import Table

from agent.cli._app import console, command


@command(global_=True)
def supervisor_check(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    json_output: bool = typer.Option(
        False, "--json", help="以 JSON 形式输出监督报告到 stdout"
    ),
) -> None:
    """长篇监督：情节推进 / 语言合规 / 风格漂移 / 伏笔回收 四维检查。

    纯规则、零 LLM、只读。advisory 语义：输出告警不阻断（阈值未标定，
    升级为阻断须先跑真实项目分位表）。
    """
    from agent.cli._shared import emit_result
    from agent.core.supervisor.supervisor import create_default_engine

    pdir = Path(project_dir)

    def _current_chapter() -> int:
        chapters = pdir / "chapters"
        if not chapters.exists():
            return 0
        nums = []
        for f in chapters.glob("ch*.md"):
            head = f.stem.find("ch")
            digits = "".join(ch for ch in f.stem[head + 2 :] if ch.isdigit())
            if digits:
                nums.append(int(digits))
        return max(nums) if nums else 0

    try:
        engine = create_default_engine(str(pdir))
        report = engine.check_all(_current_chapter())
    except Exception as e:  # noqa: BLE001 - 监督失败显性化但不崩溃
        if json_output:
            emit_result(
                {"success": False, "error": {"code": "supervisor_check_failed", "message": str(e)}},
                json_mode=True,
            )
        else:
            console.print(f"[bold red]✗ 监督检查失败[/bold red] {e}")
        raise typer.Exit(code=1) from e

    payload = {"success": True, "report": report.to_dict()}
    if json_output:
        emit_result(payload, json_mode=True)
        return

    _render(report.to_dict(), str(pdir))


def _render(report: dict, pdir: str) -> None:
    """以 rich 渲染监督报告。"""
    issues = report.get("issues", [])
    console.print(f"[bold]长篇监督[/bold] · {pdir} · {report.get('summary', '')}\n")

    if not issues:
        console.print("[bold green]✓ 四维监督通过，无告警[/bold green]")
        return

    sev_style = {"critical": "bold red", "warning": "yellow", "info": "cyan"}
    table = Table(title=f"监督告警（{len(issues)} 条）")
    table.add_column("级别", no_wrap=True)
    table.add_column("维度", style="cyan", no_wrap=True)
    table.add_column("章", justify="right", no_wrap=True)
    table.add_column("说明")
    for it in issues:
        sev = it.get("severity", "info")
        table.add_row(
            f"[{sev_style.get(sev, 'white')}]{sev}[/]",
            it.get("dimension", ""),
            str(it.get("chapter", 0)),
            it.get("message", ""),
        )
    console.print(table)
    console.print(
        "\n[dim]advisory 语义：以上为长篇结构性风险告警，不阻断写作；"
        "阈值标定后才会升级为门禁。[/dim]"
    )
