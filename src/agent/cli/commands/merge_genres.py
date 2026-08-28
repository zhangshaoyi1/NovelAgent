"""merge-genres 命令 - 多题材合并与冲突裁决

对指定项目（或显式 --genres）运行 GenreMerger，列出跨题材的设定冲突，
逐条让用户裁决（选某题材版本 / 手动合并），并将裁决结果写回 world.md
的「境界体系（冻结）」段落 —— 即收敛为本小说自己的设定。

非交互（--auto 或 Web UI 调用）时以主题材优先自动裁决并落盘。
"""

from __future__ import annotations

import re
from pathlib import Path

import frontmatter
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from agent.cli._app import app, command, console, typer
from agent.cli._shared import emit_result, enforce_gate
from agent.core.registry.genre_merger import GenreMerger, load_conflicts, save_conflicts
from agent.core.registry.genre_pack import GenrePackRegistry


def _replace_section(md_text: str, heading_prefix: str, new_body: str) -> str:
    """将 markdown 中首个以 heading_prefix 开头的顶层 ## 段落内容替换为 new_body。"""
    parts = re.split(r"(?m)^##\s+", md_text)
    out: list[str] = [parts[0]]
    replaced = False
    for seg in parts[1:]:
        lines = seg.splitlines()
        if not lines:
            out.append(seg)
            continue
        h = lines[0].strip()
        if not replaced and h.startswith(heading_prefix):
            out.append(f"## {h}\n{new_body.strip()}\n")
            replaced = True
        else:
            out.append(f"## {h}\n" + "\n".join(lines[1:]))
    if not replaced:
        out.append(f"## {heading_prefix}\n{new_body.strip()}\n")
    return "".join(out)


@command(global_=True)
def merge_genres(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    genres: str = typer.Option(
        "", "--genres", help="题材（逗号分隔，覆盖世界设定中的题材；留空则读取 world.md）"
    ),
    auto: bool = typer.Option(
        False, "--auto", help="非交互：以主题材优先自动裁决并落盘"
    ),
    decisions: str = typer.Option(
        "", "--decisions",
        help="裁决 JSON（键 'resource|section' → 数字下标或手动文本，如 "
             '{"world_template|力量体系": 1}）；给定则跳过交互与 --auto，直接应用并写回（供 Web 裁决页）',
    ),
    json_output: bool = typer.Option(
        False, "--json", help="以 JSON 输出结果到 stdout（工作流 rich 输出转 stderr）"
    ),
) -> None:
    """多题材合并与冲突裁决 - 将选中题材设定合并，冲突逐条让用户决策

    题材设定冲突（如修仙与武侠都定义「力量体系」）会被列出，用户选择保留
    某一方或手动合并，最终写回 world.md，收敛为本小说自有设定。

    Args:
        project_dir: 小说项目工作区目录
        genres: 题材列表（逗号分隔）；留空读取 world.md 的 genres
        auto: 非交互自动裁决
    """
    from agent.core.story.setting_manager import SettingManager

    pdir = Path(project_dir)
    enforce_gate(str(pdir), "merge-genres")

    # 1. 确定题材集合
    registry = GenrePackRegistry()
    available = registry.list_genres()
    if genres.strip():
        genre_list = [g.strip() for g in genres.replace("，", ",").split(",") if g.strip()]
    else:
        sm = SettingManager(pdir)
        wd = sm.load_world()
        meta = (wd.get("metadata") or {}) if wd.get("exists") else {}
        genre_list = meta.get("genres") or []
        if isinstance(genre_list, str):
            genre_list = [genre_list]
    if not genre_list:
        console.print("[red]✗ 未指定题材，且 world.md 中无 genres 记录。[/red]")
        raise typer.Exit(code=1)
    unknown = [g for g in genre_list if g not in available]
    if unknown:
        console.print(f"[yellow]提示：未知题材 {unknown}，已忽略。[/yellow]")
        genre_list = [g for g in genre_list if g in available]
    if not genre_list:
        console.print("[red]✗ 无有效题材。[/red]")
        raise typer.Exit(code=1)

    # 2. 合并
    packs = [registry.load(g) for g in genre_list]
    merger = GenreMerger()
    result = merger.merge(packs)

    if not result.conflicts:
        # 仍写回合并结果（保证单题材/无冲突时也落盘最新合并文本）
        _write_back(pdir, result, genre_list)
        if json_output:
            emit_result(
                {"success": True, "sources": result.sources,
                 "conflict_count": 0, "resolved": 0, "unresolved": 0},
                json_mode=True,
            )
        else:
            console.print(
                f"[green]✓ 题材 {[p.manifest.display_name for p in packs]} 无设定冲突，无需裁决。[/green]"
            )
        raise typer.Exit(code=0)

    console.print(
        Panel(
            f"[bold]题材合并冲突裁决[/bold]\n"
            f"参与题材：{', '.join(p.manifest.display_name for p in packs)}\n"
            f"检测到 {result.conflict_count()} 处同名设定冲突",
            border_style="yellow",
        )
    )

    # 3. 裁决
    decisions_map: dict[tuple[str, str], object] = {}
    if decisions.strip():
        # 非交互裁决：--decisions JSON（供 Web 裁决页驱动）
        import json as _json

        try:
            raw_dec = _json.loads(decisions)
        except ValueError:
            console.print("[red]✗ --decisions 不是合法 JSON。[/red]")
            raise typer.Exit(code=1)
        if not isinstance(raw_dec, dict):
            console.print("[red]✗ --decisions 必须是 JSON 对象。[/red]")
            raise typer.Exit(code=1)
        for key, val in raw_dec.items():
            if "|" not in key:
                continue
            res, sec = key.split("|", 1)
            if isinstance(val, int) or (isinstance(val, str) and val.strip()):
                decisions_map[(res.strip(), sec.strip())] = val
        console.print(f"[dim]--decisions：应用 {len(decisions_map)} 条裁决。[/dim]")
    elif auto:
        console.print("[dim]--auto：以主题材优先自动裁决。[/dim]")
        for c in result.conflicts:
            decisions_map[(c.resource, c.section)] = 0
    else:
        for c in result.conflicts:
            console.print(f"\n[bold cyan]冲突 · {c.resource} · {c.section}[/bold cyan]")
            for i, e in enumerate(c.entries):
                preview = e["content"].replace("\n", " ")[:120]
                console.print(f"  [bold]{i})[/bold] {e['label']}：{preview}")
            choice = Prompt.ask(
                "  选择保留哪一方（数字 / m 手动输入 / 回车=主题材）",
                default="0",
            ).strip()
            if choice.lower() == "m":
                manual = Prompt.ask("  请输入手动合并后的内容")
                decisions_map[(c.resource, c.section)] = manual
            elif choice.isdigit():
                idx = int(choice)
                if 0 <= idx < len(c.entries):
                    decisions_map[(c.resource, c.section)] = idx
                else:
                    console.print("[yellow]下标越界，回退主题材。[/yellow]")
            # 其它（含回车默认）→ 主题材优先，不写入决策（apply 时默认 0）

    # 4. 应用裁决并落盘
    merger.apply_decisions(result, decisions_map)
    _write_back(pdir, result, genre_list)

    if json_output:
        emit_result(
            {
                "success": True,
                "sources": result.sources,
                "conflict_count": result.conflict_count(),
                "resolved": result.conflict_count() - result.unresolved_count(),
                "unresolved": result.unresolved_count(),
            },
            json_mode=True,
        )
        return

    console.print(
        f"\n[bold green]✓ 合并完成[/bold green] 未裁决冲突：{result.unresolved_count()}；"
        f"已写入 world.md（境界体系段落）与 .state/merge_conflicts.json。"
    )
    if result.unresolved_count():
        console.print(
            "[yellow]仍有未裁决冲突（自动以主题材保留），可再次运行 /merge-genres 复核。[/yellow]"
        )


def _write_back(pdir: Path, result, genre_list: list[str]) -> None:
    """将合并后的 realm_system 写回 world.md 的「境界体系（冻结）」段落，并更新元数据。"""
    world_file = pdir / "world.md"
    if not world_file.exists():
        console.print("[red]✗ world.md 不存在，无法写回。[/red]")
        raise typer.Exit(code=1)
    post = frontmatter.load(world_file)
    # 更新元数据中的题材记录
    post.metadata["genres"] = genre_list
    try:
        reg = GenrePackRegistry()
        post.metadata["genre_label"] = " / ".join(reg.load(g).manifest.display_name for g in genre_list)
    except Exception:
        post.metadata["genre_label"] = " / ".join(genre_list)
    # 替换 realm_system 段落
    new_body = _replace_section(
        post.content, "境界体系", result.world_template
    )
    post.content = new_body
    world_file.write_text(frontmatter.dumps(post), encoding="utf-8")
    # 持久化冲突（含裁决结果）
    try:
        save_conflicts(pdir, result)
    except Exception:
        pass


# 供其它模块（如 Web UI）读取待裁决冲突的轻量封装
def pending_conflicts(project_dir: str | Path) -> dict | None:
    """返回项目的待裁决冲突（.state/merge_conflicts.json），无则 None。"""
    return load_conflicts(project_dir)
