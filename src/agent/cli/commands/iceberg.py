from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from rich.console import Console
from rich.table import Table

from agent.cli._app import app, command, console
from agent.core.story.meta.worldbuilding_schema import get_iceberg, summary, total_fields


TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "templates"


@command(global_=True)
def iceberg(
    generate: bool = False,
    dir: str = "",
    title: str = "未命名之书",
) -> None:
    """查看 / 生成「建书冰山」60+ 设定字段骨架（对标笔枢冰山模型）

    世界观 6 一级·28 子维度 + 角色系统 14 维 + 故事引擎 3 层·18 字段 = 共 60 字段。
    默认打印维度结构概览；--generate 生成 markdown 骨架（写入 <dir>/iceberg.md 或 stdout）。
    """
    c = console
    c.print(f"[bold cyan]{summary()}[/bold cyan]\n")

    t = Table(title="建书冰山维度结构")
    t.add_column("分组", style="cyan")
    t.add_column("一级维度", style="yellow")
    t.add_column("字段数", style="green")
    t.add_column("子维度 / 模型", style="white")
    for g in get_iceberg():
        t.add_row(
            g.label,
            str(g.primary_count),
            str(g.field_count),
            "、".join(d.name for d in g.dimensions),
        )
    c.print(t)

    if not generate:
        c.print("\n[dim]提示：加 --generate 可生成 markdown 建书骨架；--dir <项目目录> 写入文件。[/dim]")
        return

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(disabled_extensions=("j2",)),
        keep_trailing_newline=True,
    )
    content = env.get_template("iceberg.md.j2").render(
        title=title, groups=get_iceberg(), total=total_fields()
    )

    if dir:
        out = Path(dir) / "iceberg.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content, encoding="utf-8")
        c.print(f"\n[bold green]✓ 已生成建书冰山骨架：{out}[/bold green]")
    else:
        c.print("\n" + content)
