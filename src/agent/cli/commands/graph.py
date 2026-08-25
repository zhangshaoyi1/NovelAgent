from __future__ import annotations

from pathlib import Path

from rich.table import Table

from agent.cli._app import app, command, console, typer
from agent.core.relation_manager import NODE_KIND_LABELS, RelationManager


@command(global_=True)
def graph(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    seed: bool = typer.Option(
        False, "--seed", help="一键填充示例世界图谱（人物/势力/地点/物品/伏笔）"
    ),
    json_output: bool = typer.Option(
        False, "--json", help="以 JSON 形式输出结果到 stdout"
    ),
) -> None:
    """查看 / 填充可拖拽世界关系图谱（对标笔枢「可拖拽世界关系图谱」）

    节点按类型分为：人物(character) / 势力(faction) / 地点(location) /
    物品(item) / 伏笔(foreshadow)。可在 Web 端「关系图谱」页拖拽编排，
    本命令用于命令行快速查看或填充示例数据。
    """
    rm = RelationManager(Path(project_dir))
    if seed:
        rm.seed_sample()

    loaded = rm.load()
    if not loaded and not seed:
        console.print(
            f"[yellow]⚠[/yellow] {rm.graph_file} 不存在或为空，"
            f"先用 [cyan]--seed[/cyan] 填充示例，或到 Web 端「关系图谱」页搭建。"
        )
        return

    if json_output:
        import json

        console.print_json(
            json.dumps(rm.graph.to_dict(), ensure_ascii=False, indent=2)
        )
        return

    nt = Table(title="世界节点", title_justify="left")
    nt.add_column("ID", style="dim", no_wrap=True)
    nt.add_column("名称", style="cyan")
    nt.add_column("类型", style="green")
    nt.add_column("说明", style="white")
    for n in rm.graph.nodes:
        nt.add_row(
            n.id,
            n.name,
            NODE_KIND_LABELS.get(n.kind, n.kind),
            (n.description or "")[:40],
        )
    console.print(nt)

    et = Table(title="关系边", title_justify="left")
    et.add_column("起点", style="cyan")
    et.add_column("终点", style="cyan")
    et.add_column("关系", style="yellow")
    et.add_column("强度", justify="right", style="magenta")
    et.add_column("方向", style="dim")
    et.add_column("暗线", style="red")
    label_of = {n.id: n.name for n in rm.graph.nodes}
    for e in rm.graph.edges:
        et.add_row(
            label_of.get(e.source, e.source),
            label_of.get(e.target, e.target),
            e.relation_type or "关联",
            str(e.strength),
            "单向" if e.direction == "one" else "双向",
            "是" if e.hidden else "",
        )
    console.print(et)
    console.print(
        f"\n[bold green]✓[/bold green] 共 {len(rm.graph.nodes)} 个节点 · "
        f"{len(rm.graph.edges)} 条关系 · 存储于 [dim]{rm.graph_file}[/dim]"
    )
