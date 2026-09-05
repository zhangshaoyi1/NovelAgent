"""rewrite_paragraph 命令 —— P1-7 段落级局部重写（对标 MuMuAINovel partial-regenerate）

用法：
    novel-agent rewrite_paragraph -d <dir> --chapter 12 --paragraph 3 --instruction "收紧节奏"
    novel-agent rewrite_paragraph -d <dir> --chapter 12 --paragraph "某段原文片段" -i "..." --plan
    novel-agent rewrite_paragraph -d <dir> --chapter 12 --paragraph 3 -i "..." --apply

默认 --plan（离线 dry-run：只出定位方案与上下文窗口，不调 LLM 不落盘）；
--apply 才真正调 LLM 重写并落盘（自动备份原章 + 输出前后 diff）。
"""

from __future__ import annotations

import os
from pathlib import Path

from agent.cli._app import app, command, console, typer
from agent.cli._shared import emit_result, make_quiet_console
from agent.core.engine.state_machine import State


@command(allowed_states=(State.WRITING, State.PAUSED, State.COMPLETED))
def rewrite_paragraph(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    chapter: int = typer.Option(0, "--chapter", "-c", help="章节号（1-based）"),
    paragraph: str = typer.Option(
        "", "--paragraph", "-p",
        help="目标段落：1-based 序号（如 3）或唯一原文片段",
    ),
    instruction: str = typer.Option("", "--instruction", "-i", help="该段的修改指令"),
    apply: bool = typer.Option(
        False, "--apply", help="执行重写并落盘（默认 --plan 离线预览）"
    ),
    yes: bool = typer.Option(
        False, "--yes", help="--apply 时跳过 diff 确认直接落盘"
    ),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 形式输出结果"),
    env_file: str = typer.Option(None, "--env", help="指定 .env 文件"),
) -> None:
    """段落级局部重写 - 只改指定的一个段落，输出前后 diff，确认后落盘"""
    if env_file:
        os.environ["NOVEL_AGENT_DOTENV"] = env_file

    project_path = Path(project_dir)
    if not (project_path / "world.md").exists():
        msg = f"{project_path / 'world.md'} 不存在"
        if json_output:
            emit_result({"success": False, "error": {"code": "no_world", "message": msg}}, json_mode=True)
        else:
            console.print(f"[bold red]✗[/bold red] {msg}")
        raise typer.Exit(code=1)
    if chapter <= 0 or not paragraph.strip() or not instruction.strip():
        msg = "需要 --chapter、--paragraph 与 --instruction"
        if json_output:
            emit_result({"success": False, "error": {"code": "bad_args", "message": msg}}, json_mode=True)
        else:
            console.print(f"[bold red]✗[/bold red] {msg}")
        raise typer.Exit(code=2)

    wf_console = make_quiet_console() if json_output else console

    from agent.core.quality.rewrite.paragraph_rewriter import ParagraphRewriter

    rewriter = ParagraphRewriter(project_path, console=wf_console)

    # ---- 定位/方案（离线，永远先做；失败即止）----
    try:
        scheme = rewriter.plan(chapter, paragraph.strip(), instruction.strip())
    except (FileNotFoundError, ValueError) as e:
        if json_output:
            emit_result({"success": False, "error": {"code": "locate_failed", "message": str(e)}}, json_mode=True)
        else:
            console.print(f"[bold red]✗ 定位失败[/bold red] {e}")
        raise typer.Exit(code=1)

    if json_output and not apply:
        emit_result({"success": True, "mode": "plan", "plan": scheme}, json_mode=True)
        return

    if not apply:
        console.print(f"[bold]段落方案（--plan 预览，未调 LLM 未落盘）[/bold] 第 {chapter} 章 第 {scheme['paragraph_index']}/{scheme['total_paragraphs']} 段")
        console.print(f"[dim]上文：{scheme['context_before'][:60]}…[/dim]")
        console.print(f"[yellow]目标段：{scheme['old_paragraph']}[/yellow]")
        console.print(f"[dim]下文：{scheme['context_after'][:60]}…[/dim]")
        console.print(f"修改指令：{scheme['instruction']}")
        console.print("[dim]确认无误后加 --apply 执行重写。[/dim]")
        return

    # ---- 执行（--apply）----
    def confirm_fn(scheme: dict) -> bool:
        if yes or json_output:
            return True
        wf_console.print(f"[yellow]目标段（第 {scheme['paragraph_index']} 段）：[/yellow]{scheme['old_paragraph']}")
        try:
            ans = input("调 LLM 重写该段？[y/N]: ").strip().lower()
        except EOFError:
            return False
        return ans in ("y", "yes")

    llm = None
    if apply:
        from agent.client.gateway_adapter import create_gateway

        llm = create_gateway()
    rewriter.llm_client = llm
    result = rewriter.rewrite(chapter, paragraph.strip(), instruction.strip(), confirm_fn=confirm_fn)

    if json_output:
        emit_result({"success": result.applied, "result": result.to_dict()}, json_mode=True)
        return

    if result.error == "confirm_rejected":
        console.print("[yellow]△ 已取消（未调 LLM、未落盘）[/yellow]")
        return
    if not result.llm_used:
        console.print(f"[bold red]✗ LLM 不可用，保留原段[/bold red] {result.error}")
        return
    mark = "[green]✓ 已落盘[/green]" if result.applied else "[red]✗ 未落盘[/red]"
    wf_console.print(f"{mark} 第 {chapter} 章 第 {result.paragraph_index} 段")
    if result.diff_text:
        wf_console.print(result.diff_text)
    if result.backup_file:
        wf_console.print(f"[dim]原章备份：{result.backup_file}[/dim]")
