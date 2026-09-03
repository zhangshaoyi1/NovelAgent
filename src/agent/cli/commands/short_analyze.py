"""short-analyze 命令 —— M23 短篇拆文（外部作品分析）

对爆款短篇正文（--text / --file）做深度拆解：故事核、结构、情感线、反转设计、
写作手法、共鸣层次、可复用结构。可选 --save 把报告落盘到 <dir>/.state/analyze/。

与 m17_learn（learn 命令）的边界：本命令是**外部作品拆解**，产物仅输出报告，
不写学习库 learnings.json。

用法：
    # 从文件读取正文
    novel-agent short-analyze -f story.txt -t "重生之妻她杀疯了" -p 知乎盐言 -g 追妻
    # 直接传文本并保存报告
    novel-agent short-analyze --text "..." --title "xxx" --save -d projects/my-novel
    # JSON 输出
    novel-agent short-analyze -f story.txt --json
"""
from __future__ import annotations

import os
from pathlib import Path

from agent.cli._app import app, command, console, typer
from agent.cli._shared import emit_result, make_quiet_console


def _load_input_text(text: str, text_file: str) -> str:
    """待拆正文来源：--text 优先，其次 --file；都空则抛错。"""
    if text.strip():
        return text.strip()
    if text_file:
        f = Path(text_file)
        if not f.exists():
            raise FileNotFoundError(f"拆文正文文件不存在：{f}")
        return f.read_text(encoding="utf-8").strip()
    raise ValueError("必须提供 --text 或 --file 之一")


@command(global_=True)
def short_analyze(
    text: str = typer.Option(
        "", "--text", help="待拆短篇正文，与 --file 二选一"
    ),
    text_file: str = typer.Option(
        "", "--file", "-f", help="待拆短篇正文文件路径（UTF-8），与 --text 二选一"
    ),
    title: str = typer.Option(
        "", "--title", "-t", help="作品标题"
    ),
    platform: str = typer.Option(
        "", "--platform", "-p", help="来源平台（如 知乎盐言/七猫）"
    ),
    genre: str = typer.Option(
        "", "--genre", "-g", help="题材类型（追妻/重生/虐文...）"
    ),
    save: bool = typer.Option(
        False, "--save",
        help="把拆文报告保存到 <dir>/.state/analyze/（默认仅打印报告）",
    ),
    output_dir: str = typer.Option(
        "", "--output-dir", "-o",
        help="报告保存目录（--save 时；默认 <dir>/.state/analyze/）",
    ),
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录（用于事件接线/默认保存）"
    ),
    json_output: bool = typer.Option(
        False, "--json", help="以 JSON 形式输出结果到 stdout"
    ),
    env_file: str = typer.Option(
        None, "--env", help="指定 .env 文件（透传下游 GatewayAdapter）"
    ),
) -> None:
    """M23 短篇拆文 —— 深度拆解爆款短篇（故事核/结构/情感线/反转/手法/共鸣）"""
    if env_file:
        os.environ["NOVEL_AGENT_DOTENV"] = env_file

    project_path = Path(project_dir)
    try:
        input_text = _load_input_text(text, text_file)
    except (FileNotFoundError, ValueError) as e:
        if json_output:
            emit_result(
                {"success": False, "error": {"code": "bad_input", "message": str(e)}},
                json_mode=True,
            )
        else:
            console.print(f"[bold red]✗[/bold red] {e}")
        raise typer.Exit(code=1) from e

    # 接线：LLM 调用事件 → <project>/.events/events.jsonl（复用公共接线，避免复制）
    from agent.core.event_sourcing.llm_wiring import wire_llm_event_hook

    wire_llm_event_hook(project_path)

    workflow_console = make_quiet_console() if json_output else console
    from agent.workflows.m23_short import M23ShortAnalyzeWorkflow

    wf = M23ShortAnalyzeWorkflow(console=workflow_console)
    try:
        report = wf.run(
            input_text=input_text,
            title=title,
            platform=platform,
            genre=genre,
            save=save,
            output_dir=Path(output_dir) if output_dir else None,
        )
    except Exception as e:  # noqa: BLE001 - 拆解失败统一兜为错误信封
        if json_output:
            emit_result(
                {"success": False, "error": {"code": "analyze_failed", "message": str(e)}},
                json_mode=True,
            )
        else:
            console.print(f"[bold red]✗[/bold red] 拆文失败：{e}")
        raise typer.Exit(code=1) from e

    if json_output:
        emit_result(
            {"success": True, "report": report.to_dict()},
            json_mode=True,
        )
        return
    console.print(report.to_markdown())
    if save:
        console.print("[dim]报告已保存（--save）[/dim]")
