"""analyze 命令 —— M20 长篇拆文（外部作品深度拆解）

对长篇网文原文做 6 阶段深度拆解：概要提取 → 黄金三章 → 逐章摘要 → 聚合分析 →
设定+角色关系 → 汇总报告，产物落盘 ``{project_dir}/deconstruction/{book}/``。
支持 ``_progress.md`` 断点恢复与 ``paused_after_stage1`` 停靠续跑（续跑跳过
Stage 0/1，从 Stage 2 开始）。

用法：
    # 从原文文件开始，一次跑完（跳过 Stage 1 停靠询问）
    novel-agent analyze -d <dir> --source novel.txt --book "书名" --full
    # 只跑 Stage 0-1，产出快速预览.md 后停靠
    novel-agent analyze -d <dir> --source novel.txt --book "书名"
    # 断点续跑（从第一个未完成阶段开始；已生成过概要+预览时从 Stage 2 续跑）
    novel-agent analyze -d <dir> --book "书名" --full
    # JSON 输出
    novel-agent analyze -d <dir> --source novel.txt --full --json
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from agent.cli._app import console, typer
from agent.cli._shared import emit_result, make_quiet_console
from agent.cli.registry import command


@command(global_=True)
def analyze(
    project_dir: str = typer.Option(
        "novels/my-novel", "--dir", "-d",
        help="拆文输出根目录（产物在 <dir>/deconstruction/<book>/）",
    ),
    source: str = typer.Option(
        None, "--source", "-s",
        help="长篇原文文件路径（UTF-8；缺省用输出目录 原文/ 下已有备份）",
    ),
    book: str = typer.Option(
        None, "--book", "-b", help="书名（缺省取项目目录名）"
    ),
    stage: Optional[int] = typer.Option(
        None, "--stage", min=0, max=5,
        help="起始阶段 0-5（缺省按断点续跑：第一个未完成阶段）",
    ),
    full: bool = typer.Option(
        False, "--full",
        help="跳过 Stage 1 停靠询问，一次跑完 Stage 2-5",
    ),
    json_output: bool = typer.Option(
        False, "--json", help="以 JSON 形式输出结果到 stdout"
    ),
    env_file: str = typer.Option(
        None, "--env", help="指定 .env 文件（透传下游 GatewayAdapter）"
    ),
) -> None:
    """M20 长篇拆文 —— 6 阶段深度拆解（概要/黄金三章/逐章摘要/聚合/设定关系/报告）"""
    if env_file:
        os.environ["NOVEL_AGENT_DOTENV"] = env_file

    project_path = Path(project_dir)
    workflow_console = make_quiet_console() if json_output else console

    # 接线：LLM 调用事件 → <project>/.events/events.jsonl（复用公共接线，避免复制）
    from agent.core.event_sourcing.llm_wiring import wire_llm_event_hook
    from agent.workflows.m20_analyze import M20AnalyzeWorkflow

    wire_llm_event_hook(project_path)
    wf = M20AnalyzeWorkflow(
        project_dir=project_path, book=book, console=workflow_console
    )
    try:
        result = wf.run(source=source, stage=stage, full=full)
    except Exception as e:  # noqa: BLE001 - 拆文失败统一兜为错误信封
        if json_output:
            emit_result(
                {"success": False, "error": {"code": "analyze_failed", "message": str(e)}},
                json_mode=True,
            )
        else:
            console.print(f"[bold red]✗[/bold red] 拆文失败：{e}")
        raise typer.Exit(code=1) from e

    if json_output:
        emit_result(result.to_dict(), json_mode=True)
        return

    _render_result(result)


def _render_result(result) -> None:
    """终端呈现拆文结果摘要（非 --json 模式）。"""
    from rich.panel import Panel

    color = "green" if result.success else "red"
    paused_note = " · 已停靠（Stage 1）" if result.paused else ""
    completed = "、".join(f"Stage {s}" for s in result.completed_stages) or "无"
    console.print(
        Panel(
            f"[bold {color}]{result.status}{paused_note}[/bold {color}]\n"
            f"书名：{result.book} | 总章数：{result.total_chapters}\n"
            f"完成阶段：{completed}\n"
            f"输出目录：{result.output_dir}",
            title="长篇拆文",
            border_style=color,
        )
    )
    if result.failures:
        console.print("\n[bold red]失败记录[/bold red]")
        for f in result.failures:
            console.print(
                f"  [red]{f.get('type', '')}[/red] {f.get('ref', '')}: "
                f"{f.get('error', '')}"
            )
    if result.paused:
        console.print(
            "[dim]快速预览.md 已生成。确认继续拆解请再次运行并加 --full。[/dim]"
        )
