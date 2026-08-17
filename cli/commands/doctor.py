from __future__ import annotations

import os
from pathlib import Path

from agent.cli._app import app, console, typer, command
from agent.cli._shared import emit_result

from agent.core.doctor import Doctor, doctor_to_dict

# 状态 → rich 颜色映射（ok/info/warn/error）
_STATUS_COLOR = {
    "ok": "green",
    "info": "blue",
    "warn": "yellow",
    "error": "red",
}


@command(global_=True)
def doctor(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    json_output: bool = typer.Option(
        False, "--json",
        help="以 JSON 形式输出体检结果到 stdout",
    ),
    ping: bool = typer.Option(
        False, "--ping",
        help="探测 embedding / LLM 端点可达性（默认不联网）",
    ),
    env_file: str = typer.Option(
        None, "--env",
        help="指定 .env 文件（仅本次命令生效，透传给下游 LLMClient）",
    ),
) -> None:
    """项目健康体检（增量 F）

    只读诊断项目目录 / 状态机 / 设定集 / RAG 索引 / 依赖可用性，给出修复建议命令。
    doctor 只读取、**绝不修改**任何项目文件。

    模块：
      - structure：阶段感知的产物存在性（如 WRITING 下 chapters/ 应非空）
      - state：.state/state.json 合法性与 progress 字段
      - db：world/characters/sublines/relations/foreshadows 的存在性与 frontmatter 可解析性
      - rag：.state/rag/index.json 是否存在（长篇章节缺失则建议 reindex）
      - deps：LLM 依赖配置完整性（复用 LLMClient.preflight，不联网；--ping 才探测端点）

    --json 输出字段：success(恒为 True，表示体检已执行) / healthy / checks[]
    """
    # D：--env 透传（命令级设置环境变量，下游所有 LLMClient() 自动读取）
    if env_file:
        os.environ["NOVEL_AGENT_DOTENV"] = env_file

    project_path = Path(project_dir)
    try:
        checks = Doctor(project_path).check(ping=ping)
    except Exception as e:  # noqa: BLE001 - 体检本身不应崩溃，统一兜为错误信封
        if json_output:
            emit_result(
                {
                    "success": False,
                    "error": {
                        "code": "doctor_failed",
                        "message": f"健康体检执行失败：{e}",
                    },
                },
                json_mode=True,
            )
        else:
            console.print(f"[bold red]✗ 健康体检执行失败[/bold red] {e}")
        raise typer.Exit(code=1) from e

    healthy = Doctor.is_healthy(checks)

    if json_output:
        emit_result(
            {
                "success": True,
                "healthy": healthy,
                "checks": doctor_to_dict(checks),
            },
            json_mode=True,
        )
        return

    _render(checks, healthy)


def _render(checks: list, healthy: bool) -> None:
    """以 rich 表格渲染体检结果（非 json 模式）"""
    from rich.table import Table

    table = Table(title="项目健康体检")
    table.add_column("模块", style="cyan", no_wrap=True)
    table.add_column("状态", style="white", no_wrap=True)
    table.add_column("说明")
    table.add_column("修复命令", style="dim")

    for c in checks:
        color = _STATUS_COLOR.get(c.status, "white")
        table.add_row(
            c.module,
            f"[{color}]{c.status}[/{color}]",
            c.detail,
            c.fix_command or "",
        )

    console.print(table)
    if healthy:
        console.print("[bold green]✓ 项目健康[/bold green]")
    else:
        console.print(
            "[bold red]✗ 项目存在异常，请参考上表「修复命令」逐项处理[/bold red]"
        )
