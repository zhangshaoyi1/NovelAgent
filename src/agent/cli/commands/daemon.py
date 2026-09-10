"""writer daemon 生命周期命令（D1-2）：``daemon-start / daemon-status / daemon-stop``。"""

from __future__ import annotations

import typer

from agent.cli._app import app, console, command


@command(global_=True, help="启动 writer daemon（默认后台；--foreground 前台调试）")
def daemon_start(
    root: str = typer.Option("", "--root", help="监听的数据根（novels 目录）；缺省用 NOVEL_DATA_ROOT"),
    foreground: bool = typer.Option(False, "--foreground", "-f", help="前台运行（Ctrl+C 停止）"),
) -> None:
    from agent.daemon.core import WriterDaemon, default_root, ensure_daemon
    from agent.daemon import task_queue as tq

    data_root = root or str(default_root())
    if foreground:
        console.print(f"[bold green]writer daemon 前台启动[/bold green] root={data_root}")
        WriterDaemon([data_root]).run_forever()
        return
    if tq.daemon_alive(data_root):
        info = tq.read_heartbeat(data_root) or {}
        console.print(f"[dim]daemon 已在运行（pid={info.get('pid')}）[/dim]")
        return
    if ensure_daemon([data_root]):
        info = tq.read_heartbeat(data_root) or {}
        console.print(f"[bold green]✓[/bold green] daemon 已启动（pid={info.get('pid')}）")
    else:
        console.print("[bold red]✗[/bold red] daemon 启动失败，请查看 <root>/.daemon/ 日志")
        raise typer.Exit(code=1)


@command(global_=True, help="查看 writer daemon 运行状态与队列概览")
def daemon_status(
    root: str = typer.Option("", "--root", help="数据根；缺省用 NOVEL_DATA_ROOT"),
) -> None:
    import time

    from agent.daemon import task_queue as tq
    from agent.daemon.core import default_root

    data_root = root or str(default_root())
    info = tq.read_heartbeat(data_root)
    if not info:
        console.print(f"[yellow]daemon 未运行[/yellow]（root={data_root}）")
        return
    age = time.time() - float(info.get("ts") or 0)
    alive = age < tq.HEARTBEAT_MAX_AGE and tq.daemon_alive(data_root)
    if alive:
        state = "[bold green]运行中[/bold green]"
    elif age < tq.HEARTBEAT_MAX_AGE:
        state = "[bold red]已失联[/bold red]（心跳尚新但进程已退出，重启 Web/续写会自动重新拉起）"
    else:
        state = "[bold red]已失联[/bold red]"
    console.print(
        f"daemon pid={info.get('pid')} 心跳 {age:.0f}s 前 → {state}（root={data_root}）"
    )


@command(global_=True, help="请求 daemon 退出（当前任务完成后安全停止，不杀写进程）")
def daemon_stop(
    root: str = typer.Option("", "--root", help="数据根；缺省用 NOVEL_DATA_ROOT"),
) -> None:
    from agent.daemon import task_queue as tq
    from agent.daemon.core import default_root

    data_root = root or str(default_root())
    if not tq.daemon_alive(data_root):
        # 无存活 daemon 时不落盘停止标志：否则标志会残留，毒化下一次拉起
        # （新 daemon 启动即静默退出、队列任务永不被认领）。顺手清掉残留。
        tq.clear_daemon_stop_flag(data_root)
        console.print("[yellow]daemon 未运行，无需停止[/yellow]")
        return
    tq.request_daemon_stop(data_root)
    console.print("[bold green]✓[/bold green] 已置停止标志；daemon 将在当前任务完成后退出")
