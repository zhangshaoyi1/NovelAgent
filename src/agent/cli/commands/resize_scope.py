"""调整小说体量（篇幅）并据此重新生成大纲

用途：新建项目后想从「中篇」改成「百万字」，或改用自定义总字数/单章字数时，
用它就地更新 world.md 的体量元数据（scope / scope_total_words /
scope_chapter_length / style.chapter_length），并触发 M3 大纲重新生成。

注意：
- 体量元数据会覆盖原 world.md 里的 scope 字段；style.chapter_length
  也会被单章字数覆盖（自定义/百万字）。
- 大纲重新生成基于当前已确认架构迭代（不推进状态机，任何阶段可执行）；
  若架构尚未生成，会给出提示并停止。
- 单章字数约束 1500-5000，推荐 2000-2500。
"""

from __future__ import annotations

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *
from agent.core.engine.state_machine import State
from agent.core.story.volume import (
    MAX_CHAPTER_LENGTH,
    MIN_CHAPTER_LENGTH,
    RECOMMENDED_CHAPTER_LENGTH,
    SCOPE_LABELS,
    VALID_SCOPES,
    validate_custom,
)


@command(global_=True)
def resize_scope(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    scope: str = typer.Option(
        "long", "--scope", help="目标体量: short | medium | long | mega | custom"
    ),
    total_words: int = typer.Option(
        0, "--total-words", help="自定义体量（--scope custom）目标总字数（字）"
    ),
    chapter_length: int = typer.Option(
        0, "--chapter-length", help="单章字数（字），范围 1500-5000，推荐 2000-2500"
    ),
) -> None:
    """调整项目体量并重新生成大纲（就地改写 world.md 元数据）。"""
    from pathlib import Path

    from agent.core.story.setting_manager import SettingManager
    from agent.core.quality.guardrails import is_architecture_confirmed
    from agent.workflows.planning.m3_outline import M3OutlineWorkflow

    project_path = Path(project_dir)
    if scope not in VALID_SCOPES:
        console.print(f"[bold red]✗[/bold red] 未知体量：{scope}（可选 {', '.join(VALID_SCOPES)}）")
        raise typer.Exit(code=1)

    # 自定义体量参数校验
    if scope == "custom":
        err = validate_custom(total_words, chapter_length)
        if err:
            console.print(f"[bold red]✗[/bold red] {err}")
            raise typer.Exit(code=1)
    elif chapter_length <= 0:
        # mega/long 等：未指定单章字数则沿用会员默认（由 describe_scope 兜底）
        chapter_length = (
            None if chapter_length <= 0 else chapter_length
        )
        if scope == "mega" and chapter_length is None:
            chapter_length = 2500  # 百万字默认 2500 字/章，避免章数过多难管理
    if chapter_length is not None and (
        chapter_length < MIN_CHAPTER_LENGTH or chapter_length > MAX_CHAPTER_LENGTH
    ):
        console.print(
            f"[bold red]✗[/bold red] 单章字数需在 {MIN_CHAPTER_LENGTH}-{MAX_CHAPTER_LENGTH} "
            f"之间（推荐 {RECOMMENDED_CHAPTER_LENGTH[0]}-{RECOMMENDED_CHAPTER_LENGTH[1]} 字）。"
        )
        raise typer.Exit(code=1)

    world_file = project_path / "world.md"
    if not world_file.exists():
        console.print(
            f"[bold red]✗[/bold red] {world_file} 不存在，请先运行 start 创建项目。"
        )
        raise typer.Exit(code=1)

    # 接线：LLM 调用事件 → <project>/.events/events.jsonl（复用公共接线，避免复制）
    from agent.core.event_sourcing.llm_wiring import wire_llm_event_hook

    wire_llm_event_hook(project_path)

    sm = SettingManager(project_path)
    data = sm.load_world()
    metadata = dict(data.get("metadata", {}) or {})
    style = dict(metadata.get("style", {}) or {})
    if chapter_length:
        style["chapter_length"] = chapter_length
    metadata["scope"] = scope
    metadata["scope_label"] = SCOPE_LABELS.get(scope, scope)
    metadata["scope_total_words"] = total_words if scope == "custom" else None
    metadata["scope_chapter_length"] = chapter_length
    metadata["style"] = style
    new_file = sm.save_world(metadata, data.get("content", ""))
    detail = ""
    if scope == "custom" and total_words > 0:
        detail += f"，总字数 {total_words} 字"
    if chapter_length:
        detail += f"，单章 {chapter_length} 字"
    console.print(
        f"[bold green]✓ 体量已更新为 [{scope}]（{SCOPE_LABELS.get(scope, scope)}）"
        f"{detail}[/bold green] → {new_file}"
    )

    # 架构已确认则重新生成大纲；否则提示先走架构流程
    if not is_architecture_confirmed(project_path):
        console.print(
            "[yellow]⚠ 故事架构尚未确认，跳过大纲重新生成。"
            "请先完成架构确认后再重跑本命令或 /outline。[/yellow]"
        )
        return

    console.print("[cyan]体量已变化，正在依据新体量重新生成大纲...[/cyan]")
    wf = M3OutlineWorkflow(project_dir=project_path)
    for attempt in (1, 2):
        try:
            result = wf.run(feedback=f"按新的体量规格扩写大纲（{metadata['scope_label']}）。")
            console.print(
                f"\n[bold green]✓ 大纲已按新体量重新生成[/bold green] "
                f"共 {len(result.sublines)} 条顶层支线。"
            )
            return
        except Exception as e:
            if attempt == 1:
                console.print(
                    f"[yellow]⚠ 重新生成大纲首次失败（{e}），自动重试一次...[/yellow]"
                )
                continue
            console.print(f"\n[bold red]✗ 重新生成大纲失败[/bold red] {e}")
            raise typer.Exit(code=1) from e