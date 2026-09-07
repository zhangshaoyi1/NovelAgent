"""任务队列命令组（D1-2）：``task-submit / task-list / task-status / task-stop``。

入口降级为薄客户端的 CLI 侧：``task-submit`` 只把任务写进
``<project>/.state/tasks/pending/`` 并确保 daemon 活着，写命令由 daemon
全局串行执行（Phase 6 单一权威）。直接跑写命令（``autowrite`` 等）依然
可用——L1 派发层锁仍在，两条路径互斥不冲突。
"""

from __future__ import annotations

from pathlib import Path

import typer

from agent.cli._app import app, console, command


def _require_project(project_dir: str) -> Path:
    """解析并校验项目目录（不存在或不像项目时直接报错退出）。"""
    p = Path(project_dir).resolve()
    if not p.is_dir() or not (p / ".state").exists():
        console.print(f"[bold red]✗[/bold red] 不是有效的小说项目目录：{p}")
        raise typer.Exit(code=2)
    return p


def _known_write_commands() -> set[str]:
    import agent.cli.commands  # noqa: F401  # 触发注册副作用

    from agent.core.engine.command_router import WRITE_COMMANDS

    return set(WRITE_COMMANDS)


@command(
    global_=True,
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    help="提交写任务到 writer daemon 队列（串行执行；daemon 未起会自动拉起）",
)
def task_submit(
    ctx: typer.Context,
    command_name: str = typer.Argument(..., help="要执行的写命令，如 autowrite / write / compose"),
    project_dir: str = typer.Option(".", "--dir", "-d", help="小说项目目录"),
    submitted_by: str = typer.Option("cli", "--by", help="提交方标识（cli/web/脚本名）"),
) -> None:
    """提交任务：写 pending 文件 + 确保 daemon 存活，立即返回。"""
    from agent.daemon import task_queue as tq
    from agent.daemon.core import ensure_daemon

    extra = list(ctx.args or [])
    known = _known_write_commands()
    if command_name not in known:
        console.print(
            f"[bold red]✗[/bold red] 未知写命令：{command_name}\n"
            f"  可提交的命令：{', '.join(sorted(known))}"
        )
        raise typer.Exit(code=2)

    project = _require_project(project_dir)
    task = tq.submit_task(
        project, command_name, argv=extra, submitted_by=submitted_by
    )
    pending = sum(1 for _ in tq.iter_tasks(project, tq.STATUS_QUEUED))
    console.print(
        f"[bold green]✓[/bold green] 任务已提交：[bold]{task['task_id']}[/bold] "
        f"（{command_name}，排队数 {pending}）"
    )

    if ensure_daemon([project.parent]):
        console.print("[dim]daemon 运行中，任务将被串行消费（日志：.state/tasks/logs/）[/dim]")
    else:
        console.print(
            "[yellow]⚠ daemon 自动拉起失败；任务已排队，可手动运行 "
            "`python -m agent.daemon --root <数据根>`[/yellow]"
        )


@command(global_=True, help="列出项目任务队列中的任务")
def task_list(
    project_dir: str = typer.Option(".", "--dir", "-d", help="小说项目目录"),
) -> None:
    from agent.daemon import task_queue as tq

    project = _require_project(project_dir)
    rows = list(tq.iter_tasks(project))
    if not rows:
        console.print("[dim]队列为空[/dim]")
        return
    console.print(f"{'任务ID':<26} {'状态':<10} {'命令':<12} 提交时间")
    for t in rows:
        console.print(
            f"{t['task_id']:<26} {t.get('status', '?'):<10} "
            f"{t.get('command', '?'):<12} {t.get('submitted_at', '?')}"
        )


@command(global_=True, help="查看任务详情与执行结果")
def task_status(
    task_id: str = typer.Argument(..., help="任务 ID"),
    project_dir: str = typer.Option(".", "--dir", "-d", help="小说项目目录"),
    tail: int = typer.Option(20, "--tail", "-n", help="附带显示日志末尾行数"),
) -> None:
    from agent.daemon import task_queue as tq

    project = _require_project(project_dir)
    task = tq.get_task(project, task_id)
    if task is None:
        console.print(f"[bold red]✗[/bold red] 任务不存在：{task_id}")
        raise typer.Exit(code=2)
    import json

    console.print_json(json.dumps(task, ensure_ascii=False, indent=2))
    if task.get("status") in ("running", "done", "failed", "stopped"):
        log_path = tq.tasks_root(project) / "logs" / f"{task_id}.log"
        if log_path.exists() and tail > 0:
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            console.print(f"[dim]—— 日志末尾 {min(tail, len(lines))} 行 ——[/dim]")
            for line in lines[-tail:]:
                console.print(line)


@command(global_=True, help="停止任务：排队中的直接取消，运行中的杀进程树")
def task_stop(
    task_id: str = typer.Argument(..., help="任务 ID"),
    project_dir: str = typer.Option(".", "--dir", "-d", help="小说项目目录"),
) -> None:
    from agent.daemon import task_queue as tq

    project = _require_project(project_dir)
    result = tq.request_stop(project, task_id)
    if result == "cancelled":
        console.print(f"[bold green]✓[/bold green] 任务 {task_id} 已取消（尚在排队）")
    elif result == "signaled":
        console.print(
            f"[bold green]✓[/bold green] 已请求停止 {task_id}；daemon 将终止其进程树"
        )
    else:
        console.print(f"[bold red]✗[/bold red] 任务不存在或已是终态：{task_id}")
        raise typer.Exit(code=2)
