"""repair 命令 —— 不手动介入的消硬伤/破模板闭环（A1→A4）

把 A1 坏点采集 + A2 分层仲裁 + A3 批量重写 + A4 回归校验串成单入口：

用法：
    novel-agent repair -d <dir>                      # 默认 dry-run：只采集+分层+草拟偏好，不改正文
    novel-agent repair -d <dir> --apply              # 已确认偏好后，事实型坏点自动重写落盘
    novel-agent repair -d <dir> --apply --include-orientation  # 连取向也强制自动改（慎用）

"关键一次拍板"：
    - 首次运行（dry-run）会写 repair/preferences.md，请修改并保留确认标记后保存，
      再跑 --apply 才会自动修复事实型硬伤。
    - 取向型问题永远只进 repair/pending_decisions.md，不经确认不落盘。

默认 advisory 门禁；所有重写复用 FeedbackRewriter 备份，先打设定快照可回滚。
"""
from __future__ import annotations

import os
from pathlib import Path

from agent.cli._app import app, command, console, typer
from agent.cli._shared import enforce_gate, emit_result, make_quiet_console
from agent.core.engine.state_machine import State


@command(allowed_states=(State.WRITING, State.PAUSED, State.COMPLETED))
def repair(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    json_output: bool = typer.Option(
        False, "--json", help="以 JSON 形式输出结果到 stdout"
    ),
    env_file: str = typer.Option(
        None, "--env", help="指定 .env 文件（透传下游 LLMClient）"
    ),
    apply: bool = typer.Option(
        False, "--apply", help="已确认偏好后执行事实型自动重写（默认 dry-run 不改正文）"
    ),
    include_orientation: bool = typer.Option(
        False, "--include-orientation", help="连取向型也强制自动改（慎用，默认只进待拍板清单）"
    ),
    overwrite_preferences: bool = typer.Option(
        False, "--overwrite-preferences", help="重新草拟 preferences.md（覆盖已有）"
    ),
    no_llm_scan: bool = typer.Option(
        False, "--no-llm-scan", help="禁用 LLM 精扫，只用静态规则（CI/测试）"
    ),
) -> None:
    """消硬伤/破模板闭环 - 坏点采集→分层仲裁→批量重写→回归校验

    默认 dry-run：只采集坏点、分层、草拟书级偏好（repair/preferences.md），**不改正文**。
    加 --apply 且偏好已确认后，才自动重写事实型硬伤（先打设定快照可回滚）。
    取向型问题一律进 repair/pending_decisions.md，不经确认不落盘。
    """
    if env_file:
        os.environ["NOVEL_AGENT_DOTENV"] = env_file

    project_path = Path(project_dir)
    if not (project_path / "world.md").exists():
        if json_output:
            emit_result(
                {"success": False, "error": {"code": "no_world",
                                             "message": f"{project_path / 'world.md'} 不存在"}},
                json_mode=True,
            )
        else:
            console.print(f"[bold red]✗[/bold red] {project_path / 'world.md'} 不存在")
        raise typer.Exit(code=1)

    enforce_gate(str(project_path), "repair", json_mode=json_output)

    workflow_console = make_quiet_console() if json_output else console

    # 接线 tracer + LLM 事件（全入口统一模式）
    from agent.client import LLMClient
    from agent.core.llmops import TraceStore, TracedLLMClient, set_tracer

    set_tracer(TraceStore(project_path))
    from agent.core.event_sourcing.llm_wiring import wire_llm_event_hook

    wire_llm_event_hook(project_path)
    traced_llm = TracedLLMClient(LLMClient(), model="creative-strong")

    from agent.core.quality.repair.repair_orchestrator import RepairOrchestrator

    orc = RepairOrchestrator(
        project_path,
        llm=traced_llm,
        console=workflow_console,
        use_llm_scan=not no_llm_scan,
    )

    try:
        result = orc.run(
            dry_run=not apply,
            include_orientation=include_orientation,
            overwrite_preferences=overwrite_preferences,
        )
        if json_output:
            emit_result({"success": True, "result": result.to_dict()}, json_mode=True)
            return
        render_text(result)
    except Exception as e:
        if json_output:
            emit_result({"success": False, "error": {"code": "repair_failed", "message": str(e)}},
                        json_mode=True)
        else:
            console.print(f"[bold red]✗ repair 失败[/bold red] {e}")
        raise typer.Exit(code=1) from e


def render_text(result) -> None:
    """非 JSON 模式的可读输出。"""
    r = result
    mode = "dry-run（未改正文）" if r.dry_run else "--apply（已确认，事实型已重写）"
    console.print(f"\n[bold]=== repair {mode} ===[/bold]")
    console.print(
        f"扫描章节：{r.report.scanned_chapters} | "
        f"坏点总数：{len(r.report.points)} | "
        f"事实型自动重写：{r.summary.facts_auto_rewritten} | "
        f"取向型进待拍板：{len(r.report.by_type.get('orientation', []))} | "
        f"回归未通过：{len(r.summary.regress_failures)}"
    )
    if r.preferences_path and r.preferences_path.exists():
        console.print(f"[cyan]● 书级偏好（关键一次拍板）：{r.preferences_path}[/cyan]")
    if r.pending_path and r.pending_path.exists():
        console.print(f"[cyan]● 待拍板清单：{r.pending_path}[/cyan]")
    console.print(f"● 坏点明细：{r.pending_path.resolve().parent / 'bad_points.json' if r.pending_path else 'repair/bad_points.json'}")

    # 打印各类型坏点摘要
    by_type = r.report.by_type
    for t, pts in by_type.items():
        console.print(f"  - {t}: {len(pts)} 条" + (f"（例：{pts[0].evidence[:60]}…）" if pts else ""))
    if r.summary.llm_failures:
        console.print(f"[yellow]○ LLM 失败 {len(r.summary.llm_failures)} 处，已跳过不中断[/yellow]")
    if r.summary.regress_failures:
        console.print(f"[yellow]○ 回归未通过章节：{sorted(r.summary.regress_failures)}（有限轮已停）[/yellow]")
    if r.dry_run:
        console.print(
            "\n[bold green]下一步：[/bold green]修改并确认 repair/preferences.md（保留确认标记），"
            "然后重新运行：novel-agent repair -d <dir> --apply"
        )