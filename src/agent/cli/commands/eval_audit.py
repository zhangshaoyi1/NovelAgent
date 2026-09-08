"""eval-audit 命令（HA-Eval L5 · 可观测层）

离线回放 ``<project>/.state/quality_audit.jsonl``，把三类事故指纹
（批级串值 / 跨轮恒值 / 缓存命中）程序化点名——把「靠人工比对 token 才能定位」
变成「一条命令 5 分钟内定位」。

只读：绝不修改任何项目文件。
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.table import Table

from agent.cli._app import app, console, command


@command(global_=True)
def eval_audit(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    json_output: bool = typer.Option(
        False, "--json", help="以 JSON 形式输出回放结果到 stdout"
    ),
) -> None:
    """回放质量审计日志，检测评估维度的串值 / 恒值 / 缓存复用异常。

    读 ``.state/quality_audit.jsonl``（每次体检自动追加的维度级快照），
    识别三类事故指纹：
      - cache_collision：同轮多个 LLM 维度 value 全等且量纲混合（疑似响应复用）
      - stale_value：同一维度跨轮 value 恒等（>0，可能未真正重算）
      - cache_hit：判定类维度命中语义缓存（结果不可复用）
    """
    from agent.cli._shared import emit_result
    from agent.core.quality.audit import audit_report

    project_path = Path(project_dir)
    try:
        result = audit_report(project_path)
    except Exception as e:  # noqa: BLE001 - 回放不应崩溃
        if json_output:
            emit_result(
                {"success": False, "error": {"code": "eval_audit_failed", "message": str(e)}},
                json_mode=True,
            )
        else:
            console.print(f"[bold red]✗ 回放失败[/bold red] {e}")
        raise typer.Exit(code=1) from e

    if json_output:
        emit_result({"success": True, **result}, json_mode=True)
        return

    _render(result)


def _render(result: dict) -> None:
    """以 rich 表格渲染回放结果。"""
    console.print(
        f"[bold]质量审计回放[/bold] · 共 {result['records']} 轮体检记录\n"
    )
    anomalies = result["anomalies"]
    if not anomalies:
        console.print("[bold green]✓ 未发现串值 / 恒值 / 缓存复用异常[/bold green]")
        return

    console.print(f"[bold red]✗ 发现 {len(anomalies)} 处异常[/bold red]\n")
    table = Table(title="评估维度异常清单")
    table.add_column("轮次", style="cyan", no_wrap=True)
    table.add_column("类型", style="white", no_wrap=True)
    table.add_column("说明")

    _KIND_LABEL = {
        "cache_collision": "批级串值",
        "stale_value": "跨轮恒值",
        "cache_hit": "缓存命中",
    }
    for a in anomalies:
        table.add_row(
            str(a.get("record_index", "")),
            _KIND_LABEL.get(a.get("kind", ""), a.get("kind", "")),
            a.get("detail", ""),
        )
    console.print(table)
    console.print(
        "\n[dim]提示：批级串值/缓存命中往往意味着缓存键碰撞或响应复用，"
        "请先确认 L1 缓存语义层已默认拒绝判定类缓存；跨轮恒值建议复评确认。[/dim]"
    )
