"""deslop / deslop-chapter 命令 —— P0 去 AI 味（检测 + 改写）

对已有章节运行 6 指标 AI 味扫描（轻/中/重分级），默认仅出检测报告（dry-run），
加 ``--apply`` 才用 LLM（6 Gate + 三遍法）改写并写回章节。

用法：
    # 扫描全部章节（仅报告，不改文件）
    novel-agent deslop -d <dir>
    # 只扫描指定范围
    novel-agent deslop -d <dir> --scope 1-10 --json
    # 实际改写并写回（自动备份到 .state/deslop_backups/）
    novel-agent deslop -d <dir> --scope 1-10 --apply
    # 单章
    novel-agent deslop-chapter -d <dir> -c 12
    novel-agent deslop-chapter -d <dir> -c 12 --apply --level medium
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from agent.cli._app import app, command, console, typer
from agent.cli._shared import enforce_gate, emit_result, make_quiet_console


def _parse_scope(scope: str, max_chapter: int) -> list[int]:
    """解析 --scope：'all' / '1-10' / '1,3,5' / '3-5,8' -> 章节号列表（升序去重）。"""
    scope = (scope or "").strip().lower()
    if scope in ("", "all"):
        return list(range(1, max_chapter + 1))
    nums: set[int] = set()
    for part in scope.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            lo, hi = int(a), int(b)
            nums.update(range(lo, hi + 1))
        else:
            nums.add(int(part))
    return sorted(n for n in nums if 1 <= n <= max_chapter)


def _chapter_paths(project_dir: Path, chapters: list[int]) -> list[tuple[int, Path]]:
    """映射章节号 -> 章节文件（跳过不存在的文件）。"""
    out = []
    for n in chapters:
        f = project_dir / "chapters" / f"ch{n:03d}.md"
        if f.exists():
            out.append((n, f))
    return out


def _load_body(f: Path) -> tuple[dict, str]:
    """读取章节：返回 (frontmatter dict, 正文)。无 frontmatter 时 metadata 为空。"""
    import frontmatter

    post = frontmatter.load(f)
    return dict(post.metadata), (post.content or "").strip()


def _save_body(f: Path, meta: dict, body: str) -> None:
    """写回章节：保留原 frontmatter，替换正文。"""
    import frontmatter

    post = frontmatter.Post(body, **meta)
    f.write_text(frontmatter.dumps(post), encoding="utf-8")


def _build_report(chapters: list[tuple[int, Path]], scanner) -> list[dict]:
    """对每个章节跑 6 指标扫描，返回逐章报告。"""
    rows = []
    for n, f in chapters:
        _, body = _load_body(f)
        report = scanner.scan(body)
        rows.append(
            {
                "chapter": n,
                "level": report.level,
                "score": report.score,
                "metrics": report.metrics,
                "banned_hits": report.banned_hits[:8],
                "flagged_items": report.flagged_items,
                "word_count": len(re.sub(r"\s", "", body)),
            }
        )
    return rows


def _render_table(rows: list[dict]) -> None:
    from rich.table import Table

    table = Table(title="去 AI 味扫描报告")
    table.add_column("章节", justify="right")
    table.add_column("等级", justify="center")
    table.add_column("分数")
    table.add_column("禁用词命中")
    table.add_column("主要命中")
    for r in rows:
        color = {"light": "green", "medium": "yellow", "heavy": "red"}[r["level"]]
        table.add_row(
            f"ch{r['chapter']:03d}",
            f"[{color}]{r['level']}[/{color}]",
            f"{r['score']:.0f}",
            str(sum(h["count"] for h in r["banned_hits"])),
            "；".join(r["flagged_items"][:3]) or "-",
        )
    console.print(table)


@command(global_=True)
def deslop(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    scope: str = typer.Option(
        "all", "--scope", "-s",
        help="处理范围：all / 1-10 / 1,3,5（逗号/区间混用）",
    ),
    level: str = typer.Option(
        "auto", "--level",
        help="去 AI 味等级：auto（自动分级）/ light / medium / heavy",
    ),
    apply: bool = typer.Option(
        False, "--apply", help="实际改写并写回章节（默认仅扫描报告；写回前自动备份）"
    ),
    json_output: bool = typer.Option(
        False, "--json", help="以 JSON 形式输出结果到 stdout"
    ),
    env_file: str = typer.Option(
        None, "--env", help="指定 .env 文件（透传下游 LLMClient）"
    ),
) -> None:
    """P0 去 AI 味 —— 批量扫描/改写已有章节（6 指标分级；默认 dry-run）"""
    import os

    if env_file:
        os.environ["NOVEL_AGENT_DOTENV"] = env_file

    project_path = Path(project_dir)
    chapters_dir = project_path / "chapters"
    if not chapters_dir.exists():
        if json_output:
            emit_result(
                {"success": False, "error": {"code": "no_chapters",
                                             "message": f"{chapters_dir} 不存在"}},
                json_mode=True,
            )
        else:
            console.print(f"[bold red]✗[/bold red] {chapters_dir} 不存在")
        raise typer.Exit(code=1)

    enforce_gate(str(project_path), "deslop", json_mode=json_output)

    max_chapter = max(
        (int(f.stem[2:]) for f in chapters_dir.glob("ch*.md") if f.stem[2:].isdigit()),
        default=0,
    )
    if max_chapter == 0:
        console.print("[yellow]未找到章节文件（ch*.md）[/yellow]")
        raise typer.Exit(code=0)
    chapters = _chapter_paths(project_path, _parse_scope(scope, max_chapter))
    if not chapters:
        console.print("[bold red]✗[/bold red] 指定范围内没有已存在的章节")
        raise typer.Exit(code=1)

    workflow_console = make_quiet_console() if json_output else console

    # ---- 扫描报告（dry-run 亦先出报告）----
    from agent.core.anti_ai.detector import AIFlavorScanner

    scanner = AIFlavorScanner(project_path)
    rows = _build_report(chapters, scanner)

    # 汇总分布
    from collections import Counter

    dist = Counter(r["level"] for r in rows)

    if not apply:
        if json_output:
            emit_result(
                {
                    "success": True,
                    "dry_run": True,
                    "distribution": dict(dist),
                    "chapters": rows,
                },
                json_mode=True,
            )
        else:
            _render_table(rows)
            console.print(
                f"\n汇总：轻 {dist['light']} / 中 {dist['medium']} / 重 {dist['heavy']} "
                f"（共 {len(rows)} 章）"
            )
            console.print(
                "[dim]这是 dry-run 报告，未改动任何文件；确认后加 --apply 实际改写。[/dim]"
            )
        return

    # ---- 实际改写 ----
    from agent.core.event_sourcing.llm_wiring import wire_llm_event_hook
    from agent.core.anti_ai.rewriter import DeslopRewriter
    from agent.client import LLMClient

    wire_llm_event_hook(project_path)
    rewriter = DeslopRewriter(
        LLMClient(), project_dir=project_path, console=workflow_console
    )

    backup_dir = project_path / ".state" / "deslop_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for n, f in chapters:
        meta, body = _load_body(f)
        result = rewriter.rewrite(body, level=level)
        if result.changed and result.text.strip():
            # 备份原章
            bak = backup_dir / f"ch{n:03d}_bak.md"
            bak.write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
            _save_body(f, meta, result.text)
        results.append(
            {
                "chapter": n,
                "level": result.level,
                "changed": result.changed,
                "via_llm": result.via_llm,
                "backup": str(backup_dir / f"ch{n:03d}_bak.md") if result.changed else None,
            }
        )

    if json_output:
        emit_result(
            {"success": True, "apply": True, "distribution": dict(dist), "results": results},
            json_mode=True,
        )
        return
    _render_table(rows)
    changed = sum(1 for r in results if r["changed"])
    console.print(
        f"\n汇总：轻 {dist['light']} / 中 {dist['medium']} / 重 {dist['heavy']} "
        f"（共 {len(rows)} 章）；改写 {changed} 章"
    )
    console.print(f"[dim]原章备份：{backup_dir}[/dim]")


@command(global_=True)
def deslop_chapter(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    chapter: int = typer.Option(
        0, "--chapter", "-c", help="章节号（1-based）"
    ),
    level: str = typer.Option(
        "auto", "--level",
        help="去 AI 味等级：auto（自动分级）/ light / medium / heavy",
    ),
    apply: bool = typer.Option(
        False, "--apply", help="实际改写并写回该章（默认仅扫描；写回前自动备份）"
    ),
    json_output: bool = typer.Option(
        False, "--json", help="以 JSON 形式输出结果到 stdout"
    ),
    env_file: str = typer.Option(
        None, "--env", help="指定 .env 文件（透传下游 LLMClient）"
    ),
) -> None:
    """P0 去 AI 味 —— 单章扫描/改写（6 指标分级；默认 dry-run）"""
    import os

    if env_file:
        os.environ["NOVEL_AGENT_DOTENV"] = env_file

    project_path = Path(project_dir)
    if chapter <= 0:
        if json_output:
            emit_result(
                {"success": False, "error": {"code": "bad_args", "message": "需要 --chapter"}},
                json_mode=True,
            )
        else:
            console.print("[bold red]✗[/bold red] 需要 --chapter")
        raise typer.Exit(code=2)

    f = project_path / "chapters" / f"ch{chapter:03d}.md"
    if not f.exists():
        if json_output:
            emit_result(
                {"success": False, "error": {"code": "chapter_not_found", "message": str(f)}},
                json_mode=True,
            )
        else:
            console.print(f"[bold red]✗[/bold red] 章节文件不存在：{f}")
        raise typer.Exit(code=1)

    enforce_gate(str(project_path), "deslop_chapter", json_mode=json_output)

    workflow_console = make_quiet_console() if json_output else console

    from agent.core.anti_ai.detector import AIFlavorScanner

    scanner = AIFlavorScanner(project_path)
    meta, body = _load_body(f)
    report = scanner.scan(body)

    if not apply:
        if json_output:
            emit_result(
                {
                    "success": True,
                    "dry_run": True,
                    "chapter": chapter,
                    "level": report.level,
                    "score": report.score,
                    "metrics": report.metrics,
                    "banned_hits": report.banned_hits[:8],
                    "flagged_items": report.flagged_items,
                },
                json_mode=True,
            )
        else:
            color = {"light": "green", "medium": "yellow", "heavy": "red"}[report.level]
            console.print(
                f"\n[bold]第 {chapter} 章 · AI 味[/bold] "
                f"[{color}]{report.level}[/{color}]（分数 {report.score:.0f}）"
            )
            if report.banned_hits:
                hits = "、".join(
                    f"{h['word']}×{h['count']}" for h in report.banned_hits[:8]
                )
                console.print(f"[yellow]禁用词命中：[/yellow]{hits}")
            for m_name, m in report.metrics.items():
                console.print(f"  {m_name}: {m['value']}（{m['level']}）")
            if report.flagged_items:
                console.print(f"[yellow]主要问题：[/yellow]" + "；".join(report.flagged_items))
            console.print("[dim]这是 dry-run 报告，未改动文件；加 --apply 实际改写。[/dim]")
        return

    from agent.core.event_sourcing.llm_wiring import wire_llm_event_hook
    from agent.core.anti_ai.rewriter import DeslopRewriter
    from agent.client import LLMClient

    wire_llm_event_hook(project_path)
    rewriter = DeslopRewriter(
        LLMClient(), project_dir=project_path, console=workflow_console
    )
    result = rewriter.rewrite(body, level=level)

    backup = None
    if result.changed and result.text.strip():
        backup_dir = project_path / ".state" / "deslop_backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / f"ch{chapter:03d}_bak.md"
        backup.write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
        _save_body(f, meta, result.text)

    if json_output:
        emit_result(
            {
                "success": True,
                "apply": True,
                "chapter": chapter,
                "level": result.level,
                "changed": result.changed,
                "via_llm": result.via_llm,
                "backup": str(backup) if backup else None,
            },
            json_mode=True,
        )
        return
    if result.changed:
        console.print(
            f"[green]✓ 第 {chapter} 章已改写（{result.level}）[/green]"
        )
        if backup:
            console.print(f"[dim]原章备份：{backup}[/dim]")
        for c in result.changes[:10]:
            console.print(f"  - {c[:80]}")
    else:
        console.print(
            f"[yellow]△ 第 {chapter} 章未改动（等级 {result.level}，"
            f"{'改写失败保留原文' if result.via_llm else '无需修改'}）[/yellow]"
        )
