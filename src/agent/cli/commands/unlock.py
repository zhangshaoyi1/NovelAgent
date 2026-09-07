"""unlock 命令：安全清理项目写锁（writer.lock）——D4 的便宜替代。

背景（2026-09-07 复盘）：F-8 保守判活（ACCESS_DENIED 视为存活）+
atexit 释放依赖，使硬杀/崩溃后的陈旧锁只能人工找文件删。
本命令把「人工删文件」升级为「一键安全操作」：

- 持有者已死 → 直接删除（安全，这正是陈旧锁的正确处置）；
- 持有者存活 → **拒绝**（此时锁代表真实在写的进程，删锁即打开并发写缺口，
  五灵破归档事故同款），需 ``--force`` 双重确认才可覆盖；
- 兼容清理历史按命令命名的遗留锁（autowrite.lock 等）。

本命令只动锁文件、不写章节内容，因此**不**标记 writes=True（不进队列路由）。
"""

from __future__ import annotations

import typer

from agent.cli._app import app, command, console


@command(global_=True, help="安全清理项目写锁（持有者已死时删除；存活时拒绝）")
def unlock(
    project_dir: str = typer.Option(".", "--dir", "-d", help="小说项目目录"),
    force: bool = typer.Option(
        False, "--force",
        help="持有者仍存活时强制删锁（危险：可能重新打开并发写缺口，需确认进程确已死）",
    ),
) -> None:
    """检查并清理 <project>/.state/writer.lock。

    持有者已死（陈旧锁）→ 自动删除并报告；持有者存活 → 拒绝并给出处置建议
    （task-stop / taskkill），``--force`` 可越过但会显著警告。
    """
    import json
    import os
    from pathlib import Path

    from agent.core.project_lock import (
        LEGACY_LOCK_NAMES,
        _pid_alive,
        lock_path_for,
    )

    p = Path(project_dir).resolve()
    if not p.is_dir():
        console.print(f"[bold red]✗[/bold red] 目录不存在：{p}")
        raise typer.Exit(code=2)

    candidates = [lock_path_for(p)] + [p / ".state" / n for n in LEGACY_LOCK_NAMES]
    existing = [c for c in candidates if c.exists()]
    if not existing:
        console.print(f"[green]✓[/green] 项目无写锁：{p / '.state'}")
        return

    for lock_path in existing:
        info: dict = {}
        try:
            info = json.loads(lock_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass  # noqa: SILENT_DEGRADE - 空损锁文件按陈旧处理
        pid = int(info.get("pid") or 0)
        holder = (
            f"pid={pid} cmd={info.get('command', '?')} "
            f"started={info.get('started_at', '?')}"
        )
        if pid == os.getpid():
            console.print(f"[yellow]跳过[/yellow] {lock_path.name}：本进程自身持有")
            continue
        if _pid_alive(pid) and not force:
            console.print(
                f"[bold red]✗ 拒绝解锁[/bold red] {lock_path.name}：持有者存活（{holder}）\n"
                f"  锁代表一个正在写的进程，强行删除会打开并发写缺口（章节互相覆盖）。\n"
                f"  正确处置：[cyan]agent task-stop <task_id>[/cyan] 停止 daemon 任务，"
                f"或 [cyan]taskkill /PID {pid} /T /F[/cyan] 终止直跑进程；\n"
                f"  若确认该 pid 已死（Windows 判活受限可误报），用 "
                f"[cyan]agent unlock -d {p} --force[/cyan] 覆盖。"
            )
            raise typer.Exit(code=2)
        if _pid_alive(pid) and force:
            console.print(
                f"[bold yellow]⚠ --force 覆盖存活判定[/bold yellow]：删除 {lock_path.name}"
                f"（持有者 {holder}）。若该进程仍在写，将出现并发写！"
            )
        else:
            console.print(f"[cyan]陈旧锁[/cyan] {lock_path.name}：持有者已死（{holder}）")
        try:
            lock_path.unlink()
        except OSError:
            pass  # noqa: SILENT_DEGRADE - 以文件实际存在性为准（见下）
        if lock_path.exists():
            console.print("[bold red]✗ 删除失败：锁文件仍存在[/bold red]")
            raise typer.Exit(code=1)
        console.print(f"[bold green]✓ 已删除[/bold green] {lock_path}")
