"""前台跟随（D3-2）：CLI 提交任务后在本进程跟踪执行直至终态。

设计要点：
- **流式回放**：增量读取任务日志文件（daemon 捕获的子进程 stdout/stderr），
  打印到本地 stdout——输出形态与直跑时一致，自动化脚本零改动。
- **Ctrl+C = 优雅停止**：中断跟随不杀 daemon 子进程，而是写 ``stop_requested``
  标记（与 task-stop/Web stop 同一语义），随后继续跟随直至任务终态；
  再次 Ctrl+C 才强制退出。
- **心跳守护**：daemon 心跳长期丢失（>120s）时放弃跟随并返回码 3——
  任务仍保留在队列/running 中，daemon 恢复后会继续处理或标 failed，
  绝不静默挂死。
- **退出码透传**：done→0；stopped→130；failed→子进程退出码（无则 1）。
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

#: 剥离 rich 标记（子进程写日志文件时非 tty，一般无 ANSI，但异常路径可能残留）
_RICH_TAG_RE = re.compile(r"\[/?[a-zA-Z][^\[\]]{0,60}\]")

#: 心跳丢失放弃跟随的阈值（秒）。daemon 每个轮询周期（1s）刷新心跳。
HEARTBEAT_STALE_ABORT = 120


def _strip_rich(text: str) -> str:
    return _RICH_TAG_RE.sub("", text)


def _drain_log(log_path: Path, pos: int, *, echo: bool) -> int:
    """增量读取日志并打印，返回新读取位置。"""
    if not log_path.exists():
        return pos
    size = log_path.stat().st_size
    if size <= pos:
        return pos
    with log_path.open("r", encoding="utf-8", errors="replace") as f:
        f.seek(pos)
        chunk = f.read()
        pos = f.tell()
    if echo:
        for line in chunk.splitlines():
            print(_strip_rich(line), flush=True)
    return pos


def follow_task(
    project_dir: Path | str,
    task_id: str,
    *,
    poll_interval: float = 0.5,
    echo: bool = True,
    daemon_root: Path | str | None = None,
) -> int:
    """跟随任务直至终态，返回退出码（供 typer.Exit）。

    Args:
        project_dir: 小说项目目录（任务所在队列）。
        task_id: 任务 ID。
        echo: 是否回放日志到 stdout（``--json`` 场景调用方不应路由至此）。
        daemon_root: daemon 心跳所在数据根（默认项目父目录）。
    """
    from agent.daemon import task_queue as tq

    project_dir = Path(project_dir)
    root = Path(daemon_root) if daemon_root else project_dir.parent
    log_path = tq.tasks_root(project_dir) / "logs" / f"{task_id}.log"

    pos = 0
    stop_requested = False
    ever_alive = False

    while True:
        try:
            task = tq.get_task(project_dir, task_id) or {}
            pos = _drain_log(log_path, pos, echo=echo)
            status = task.get("status")

            if status in tq.TERMINAL_STATUSES:
                pos = _drain_log(log_path, pos, echo=echo)  # 收尾增量
                if echo:
                    note = task.get("note") or ""
                    tail = f"（{note}）" if note else ""
                    print(
                        f"■ 任务 {task_id} 结束：{status}{tail}",
                        flush=True,
                    )
                if status == tq.STATUS_DONE:
                    return 0
                if status == tq.STATUS_STOPPED:
                    return 130
                rc = task.get("exit_code")
                return int(rc) if isinstance(rc, int) and rc != 0 else 1

            if not stop_requested and task.get("stop_requested"):
                stop_requested = True
                if echo:
                    print("… 已收到停止请求，等待 daemon 终止任务进程树", flush=True)

            alive = tq.heartbeat_alive(root)
            if alive:
                ever_alive = True
            elif ever_alive:
                # daemon 曾活过但心跳长期丢失：放弃跟随（任务保留，不静默挂死）
                if echo:
                    print(
                        f"✗ daemon 心跳丢失超过 {HEARTBEAT_STALE_ABORT}s，放弃跟随；"
                        f"任务 {task_id} 保留在队列中，daemon 恢复后将继续处理"
                        f"（可随时 task-status {task_id} 查询）",
                        file=sys.stderr,
                        flush=True,
                    )
                return 3

            time.sleep(poll_interval)
        except KeyboardInterrupt:
            # 有意控制流：第一击 Ctrl+C 转 stop_requested 优雅停止（与 task-stop
            # / Web stop 同语义），第二击才强制退出跟随。非异常吞噬。
            if not stop_requested:
                tq.request_stop(project_dir, task_id)
                stop_requested = True
                if echo:
                    print(
                        "\n⏸ 已请求停止任务（Ctrl+C 再次按下将强制退出跟随，"
                        "daemon 会继续收尾）",
                        flush=True,
                    )
            else:
                if echo:
                    print("✗ 强制退出跟随；任务停止流程仍在 daemon 侧继续", file=sys.stderr)
                return 130  # noqa: SILENT_DEGRADE
