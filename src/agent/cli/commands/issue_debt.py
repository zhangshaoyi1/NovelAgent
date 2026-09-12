"""issue-debt 命令 —— 问题债务登记簿（2026-09-12）

确认待解决的问题（WARN 一致性警告、门禁告警留章、离场/死亡角色禁出场等）
登记成"债务"：写时自动注入约束提醒 writer，presence_ban 类由一致性门禁
强制执行（被禁主体出场即 BLOCK），显性销账后解除。

用法：
    novel-agent issue-debt -d <dir> list                     # 列出未销账债务
    novel-agent issue-debt -d <dir> add --kind presence_ban \\
        --subject 柳依依 --ch 10 \\
        --constraint "柳依依已在第8章前往北境，当前地图禁止出场"   # 登记禁出场
    novel-agent issue-debt -d <dir> add --kind watch \\
        --constraint "第10章世界之树位置描写与第3章矛盾，待定稿统一"  # 登记观察项
    novel-agent issue-debt -d <dir> resolve DEBT-0003 --note "已重写第10章"  # 销账
"""

from __future__ import annotations

from pathlib import Path

from agent.cli._app import app, command, console, typer
from agent.core.story.issue_debt import (
    KIND_GATE_SKIPPED,
    KIND_PRESENCE_BAN,
    KIND_WATCH,
    IssueDebtStore,
)


def _load(project_dir: str) -> IssueDebtStore:
    store = IssueDebtStore(Path(project_dir))
    store.load()
    return store


@command(global_=True)
def issue_debt(
    project_dir: str = typer.Option(".", "--dir", "-d", help="小说项目目录"),
    action: str = typer.Argument("list", help="list | add | resolve"),
    kind: str = typer.Option(
        KIND_WATCH, "--kind", "-k",
        help=f"债务类型：{KIND_PRESENCE_BAN}（禁出场，门禁强制）/ {KIND_WATCH}（观察提醒）/ {KIND_GATE_SKIPPED}",
    ),
    subject: str = typer.Option("", "--subject", "-s", help="被禁主体名（presence_ban 必填）"),
    constraint: str = typer.Option("", "--constraint", "-c", help="约束/问题描述（add 必填）"),
    chapter: int = typer.Option(0, "--ch", help="登记时章节号（用于账龄标注）"),
    debt_id: str = typer.Argument("", help="resolve：要销账的债务 ID（如 DEBT-0003）"),
    note: str = typer.Option("", "--note", help="resolve：销账说明"),
) -> None:
    """确认的问题登记 / 查询 / 销账（问题债务登记簿）"""
    store = _load(project_dir)
    if action == "list":
        items = store.open_items()
        if not items:
            console.print("[green]✓ 无未销账问题债务[/green]")
            return
        console.print(f"[bold]未销账问题债务（{len(items)} 条）[/bold]")
        for d in items:
            tag = {"presence_ban": "禁出场", "watch": "观察", "gate_skipped": "门禁跳过"}.get(
                d.kind, d.kind
            )
            console.print(
                f"  [red]{d.id}[/red] [{tag}] 第{d.registered_ch or '?'}章登记 "
                f"{('主体：' + d.subject) if d.subject else ''}\n"
                f"      {d.constraint}"
            )
        return
    if action == "add":
        if not constraint.strip():
            console.print("[red]✗ --constraint 不能为空[/red]")
            raise typer.Exit(code=2)
        debt = store.add(
            kind, constraint,
            subject=subject, registered_ch=chapter,
        )
        store.save()
        console.print(f"[bold green]✓ 已登记 {debt.id}[/bold green]（{debt.kind}）")
        if debt.kind == KIND_PRESENCE_BAN:
            console.print("[dim]该禁令将由一致性门禁 presence_conflict 规则强制执行。[/dim]")
        return
    if action == "resolve":
        if not debt_id:
            console.print("[red]✗ 请提供要销账的债务 ID[/red]")
            raise typer.Exit(code=2)
        if store.resolve(debt_id, note):
            store.save()
            console.print(f"[bold green]✓ 已销账 {debt_id}[/bold green]")
        else:
            console.print(f"[red]✗ 未找到 open 状态的债务 {debt_id}[/red]")
            raise typer.Exit(code=2)
        return
    console.print(f"[red]✗ 未知 action：{action}（list / add / resolve）[/red]")
    raise typer.Exit(code=2)
