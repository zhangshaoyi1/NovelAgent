"""book-checkup 命令（全书体检 · 纯规则离线指标）

单章质检只有"本章+相邻 2 章"视野，本书体检提供全书视野的确定性指标：
pressure_stage 连续同值 / 伏笔账龄 / 配角连续出场 / 境界推进速率 /
章末钩子句式重复 / 章节字数分布。

只读：绝不修改任何项目文件。对应登记：项目文档/优化/20260913_灵荒炉火差评复盘.md T4。
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.table import Table

from agent.cli._app import app, console, command


@command(global_=True)
def book_checkup(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    json_output: bool = typer.Option(
        False, "--json", help="以 JSON 形式输出体检结果到 stdout"
    ),
    stage_streak_limit: int = typer.Option(
        8, "--stage-streak-limit", help="pressure_stage 连续同值章数上限"
    ),
    char_streak_limit: int = typer.Option(
        10, "--char-streak-limit", help="配角连续出场章数上限"
    ),
    hook_similarity: float = typer.Option(
        0.85, "--hook-similarity", help="章末钩子近重复相似度阈值"
    ),
    foreshadow_grace: int = typer.Option(
        10, "--foreshadow-grace", help="伏笔账龄宽限章数"
    ),
    min_chapter_chars: int = typer.Option(
        1500, "--min-chapter-chars", help="超短章阈值（正文字符数）"
    ),
) -> None:
    """全书体检：pressure_stage 连续同值 / 伏笔账龄 / 配角停滞 / 境界推进 / 章末钩子重复 / 字数分布。

    纯规则、零 LLM、只读。任一指标违规即 passed=False（失败显性化）。
    """
    from agent.cli._shared import emit_result
    from agent.core.quality.book_checkup import run_book_checkup

    try:
        result = run_book_checkup(
            Path(project_dir),
            stage_streak_limit=stage_streak_limit,
            char_streak_limit=char_streak_limit,
            hook_similarity=hook_similarity,
            foreshadow_grace=foreshadow_grace,
            min_chapter_chars=min_chapter_chars,
        )
    except Exception as e:  # noqa: BLE001 - 体检失败显性化但不崩溃
        if json_output:
            emit_result(
                {"success": False, "error": {"code": "book_checkup_failed", "message": str(e)}},
                json_mode=True,
            )
        else:
            console.print(f"[bold red]✗ 体检失败[/bold red] {e}")
        raise typer.Exit(code=1) from e

    if json_output:
        emit_result(result, json_mode=True)
        return

    _render(result)


def _render(result: dict) -> None:
    """以 rich 表格渲染全书体检结果。"""
    if not result.get("success"):
        console.print(f"[bold red]✗ {result.get('error', {}).get('message', '体检失败')}[/bold red]")
        return

    console.print(
        f"[bold]全书体检[/bold] · ch{result['first_chapter']:03d}-ch{result['last_chapter']:03d}"
        f" 共 {result['chapter_count']} 章\n"
    )

    table = Table(title="指标概览")
    table.add_column("指标", style="cyan", no_wrap=True)
    table.add_column("关键读数")
    for m in result["metrics"]:
        if m["metric"] == "stage_streak":
            worst = max((v["length"] for v in m["violations"]), default=0)
            table.add_row(m["label"], f"最长连续同值 {worst} 章（上限 {m['limit']}）")
        elif m["metric"] == "foreshadow_aging":
            table.add_row(
                m["label"],
                f"当前 ch{m['current_chapter']:03d} · 逾期 {len(m['overdue'])} · 该埋未埋 {len(m['unburied'])}",
            )
        elif m["metric"] == "character_stagnation":
            worst = max((v["length"] for v in m["violations"]), default=0)
            table.add_row(
                m["label"], f"登记角色 {m['characters']} 个 · 最长连续出场 {worst} 章（上限 {m['limit']}）"
            )
        elif m["metric"] == "realm_progression":
            per = m["chapters_per_advance"] or "-"
            table.add_row(
                m["label"],
                f"突破事件 {m['advance_count']} 次 · 平均 {per} 章/次 · 距最近一次 {m['chapters_since_last_advance']} 章",
            )
        elif m["metric"] == "ending_hooks":
            total = sum(c["count"] for c in m["clusters"])
            table.add_row(
                m["label"], f"近重复结尾簇 {len(m['clusters'])} 个 · 涉及 {total} 章（阈值 {m['similarity']}）"
            )
        elif m["metric"] == "ending_cliche":
            table.add_row(
                m["label"], f"套话命中 {m['hit_count']} 章（末 {m['tail_lines']} 行窗口）"
            )
        elif m["metric"] == "entity_drift":
            table.add_row(
                m["label"],
                f"正典外实体 {len(m['unknown'])} 个"
                + (f"（{', '.join(u['entity'] for u in m['unknown'][:4])}）" if m["unknown"] else ""),
            )
        elif m["metric"] == "rename_drift":
            table.add_row(
                m["label"],
                f"疑似改名 {len(m['suspects'])} 处"
                + (f"（{', '.join(s['registered'] + '→' + s['alias'] for s in m['suspects'][:3])}）" if m["suspects"] else ""),
            )
        elif m["metric"] == "speaker_registry":
            table.add_row(
                m["label"],
                f"未登记 recurring 说话人 {len(m['unregistered'])} 个"
                + (f"（{', '.join(u['speaker'] for u in m['unregistered'][:4])}）" if m["unregistered"] else ""),
            )
        elif m["metric"] == "word_count":
            if m.get("count"):
                table.add_row(
                    m["label"],
                    f"min {m['min']} / mean {m['mean']} / max {m['max']} 字 · 超短章 {len(m['undersized'])}",
                )
    console.print(table)

    issues = result["issues"]
    degraded = result["degraded"]
    if not issues and not degraded:
        console.print("\n[bold green]✓ 全书体检通过，未发现结构性问题[/bold green]")
        return

    if issues:
        console.print(f"\n[bold red]✗ 发现 {len(issues)} 处结构性问题[/bold red]\n")
        issue_table = Table(title="问题清单")
        issue_table.add_column("指标", style="cyan", no_wrap=True)
        issue_table.add_column("说明")
        for it in issues:
            issue_table.add_row(it["metric"], it["detail"])
        console.print(issue_table)
    if degraded:
        console.print(f"\n[bold yellow]⚠ {len(degraded)} 项数据降级（未被计入通过）[/bold yellow]")
        for d in degraded:
            console.print(f"  - {d}")
    console.print(
        "\n[dim]提示：以上为全书视野的结构性风险，与单章质检互补；"
        "调参见 --stage-streak-limit / --char-streak-limit / --hook-similarity 等选项。[/dim]"
    )
