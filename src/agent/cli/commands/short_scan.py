"""short-scan 命令 —— M23 短篇扫榜（外部市场分析）

对榜单样本（--input 文件 / --text 直接输入）做短篇网文市场分析，输出情绪方向、
题材候选、风险阈值与验证动作。也可不提供样本，基于内置市场知识
（skills/short-story/real-market-data.md）输出候选假设（标注需复扫校验）。

与 m17_learn（learn 命令）的边界：本命令是**外部短篇市场分析**，产物仅输出报告
（可选 --save 落盘到 <dir>/.state/analyze/），不写学习库 learnings.json。

用法：
    # 从文件读取榜单样本
    novel-agent short-scan -i leaderboard.txt -p 知乎盐言
    # 直接传文本
    novel-agent short-scan --text "..." --platform 七猫 --json
    # 仅基于内置知识（输出标注为候选假设）
    novel-agent short-scan -p 综合
"""
from __future__ import annotations

import os
from pathlib import Path

from agent.cli._app import app, command, console, typer
from agent.cli._shared import emit_result, make_quiet_console


def _load_market_data(input_file: str, text: str) -> str:
    """榜单样本来源：--input 文件优先，其次 --text；都空则返回 ''（走内置知识）。"""
    if input_file:
        f = Path(input_file)
        if not f.exists():
            raise FileNotFoundError(f"榜单样本文件不存在：{f}")
        return f.read_text(encoding="utf-8").strip()
    return (text or "").strip()


@command(global_=True)
def short_scan(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录（用于事件接线）"
    ),
    input_file: str = typer.Option(
        "", "--input", "-i", help="榜单样本文件路径（UTF-8），与 --text 二选一"
    ),
    text: str = typer.Option(
        "", "--text", help="榜单样本原文，与 --input 二选一；都不给则基于内置知识"
    ),
    platform: str = typer.Option(
        "综合", "--platform", "-p", help="目标平台（知乎盐言/七猫/黑岩/点众/综合）"
    ),
    sample_date: str = typer.Option(
        "", "--sample-date", help="样本日期（默认今天）"
    ),
    save: bool = typer.Option(
        False, "--save",
        help="把扫榜报告保存到 <dir>/.state/analyze/（默认仅打印报告）",
    ),
    json_output: bool = typer.Option(
        False, "--json", help="以 JSON 形式输出结果到 stdout"
    ),
    env_file: str = typer.Option(
        None, "--env", help="指定 .env 文件（透传下游 LLMClient）"
    ),
) -> None:
    """M23 短篇扫榜 —— 短篇网文外部市场分析（情绪/题材/风口）"""
    if env_file:
        os.environ["NOVEL_AGENT_DOTENV"] = env_file

    project_path = Path(project_dir)
    try:
        market_data = _load_market_data(input_file, text)
    except FileNotFoundError as e:
        if json_output:
            emit_result(
                {"success": False, "error": {"code": "input_not_found", "message": str(e)}},
                json_mode=True,
            )
        else:
            console.print(f"[bold red]✗[/bold red] {e}")
        raise typer.Exit(code=1) from e

    # 接线：LLM 调用事件 → <project>/.events/events.jsonl（复用公共接线，避免复制）
    from agent.core.event_sourcing.llm_wiring import wire_llm_event_hook

    wire_llm_event_hook(project_path)

    workflow_console = make_quiet_console() if json_output else console
    from agent.workflows.m23_short import M23ShortScanWorkflow

    wf = M23ShortScanWorkflow(console=workflow_console)
    try:
        report = wf.run(
            market_data=market_data,
            platform=platform,
            sample_date=sample_date,
        )
    except Exception as e:  # noqa: BLE001 - 分析失败统一兜为错误信封
        if json_output:
            emit_result(
                {"success": False, "error": {"code": "scan_failed", "message": str(e)}},
                json_mode=True,
            )
        else:
            console.print(f"[bold red]✗[/bold red] 扫榜失败：{e}")
        raise typer.Exit(code=1) from e

    if save:
        out = _save_report(report, project_path)
    else:
        out = None

    if json_output:
        emit_result(
            {"success": True, "saved_to": str(out) if out else None, "report": report.to_dict()},
            json_mode=True,
        )
        return
    console.print(report.to_markdown())
    if out:
        console.print(f"[dim]已保存到 {out}[/dim]")


def _save_report(report, project_path: Path) -> Path:
    """把扫榜报告 Markdown 落盘到 <dir>/.state/analyze/。"""
    out_dir = project_path / ".state" / "analyze"
    out_dir.mkdir(parents=True, exist_ok=True)
    from datetime import datetime

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"scan-{report.platform or 'market'}-{ts}.md"
    path.write_text(report.to_markdown(), encoding="utf-8")
    return path
