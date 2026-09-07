"""Writer daemon 消费循环（D2-1）：全局串行的唯一写执行权威。

核心语义：
- **全局串行**：同一时刻整个系统最多一个写任务在执行（跨项目也串行）。
  这是比「每项目串行」更强的保证——D2 阶段优先正确性，多项目并行作为
  后续优化（每项目一把执行线程 + 项目级队列即可扩展）。
- **进程内是编排、执行是子进程**：daemon 持久运行，spawn
  ``python -m agent.cli <command>`` 子进程执行任务（复用全部既有 CLI
  逻辑，含 L1 派发层加锁）。daemon 长寿命持有子进程句柄 → 停止 =
  taskkill /T /F 杀进程树；daemon 自身退出不产生孤儿（子进程随任务
  归档，daemon 崩溃重启时孤儿任务标记 failed）。
- **writer.lock 降级为断言**：daemon 队列已保证互斥，子进程内的
  acquire_project_lock 只防「绕过 daemon 的遗留直跑者」（--direct）。
- **心跳**：每个轮询周期刷新 ``<root>/.daemon/heartbeat.json``，
  ``task submit`` / Web 据此判断 daemon 是否存活、是否需要自动拉起。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from agent.daemon import task_queue as tq

#: agent 仓库根（含 src/ 与 scripts/）：本文件位于 <root>/src/agent/daemon/core.py
REPO_ROOT = Path(__file__).resolve().parents[3]


def default_root() -> Path:
    """默认监听的数据根：NOVEL_DATA_ROOT 优先，否则 <仓库根>/../novels。"""
    import os

    env_root = os.environ.get("NOVEL_DATA_ROOT")
    if env_root:
        return Path(env_root)
    return REPO_ROOT.parent / "novels"


def kill_process_tree_pid(pid: int) -> None:
    """按 PID 终止进程树（Windows 用 taskkill /T /F；POSIX 退回 killpg）。"""
    if pid <= 0:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
                check=False,
            )
            return
        except Exception:  # noqa: BLE001 - taskkill 失败退回下面的通用兜底
            pass  # noqa: SILENT_DEGRADE
    try:
        if hasattr(os, "killpg"):
            import signal

            os.killpg(pid, signal.SIGTERM)
        else:
            os.kill(pid, 15)
    except (ProcessLookupError, PermissionError, OSError):
        pass  # noqa: SILENT_DEGRADE - 进程已退出


class WriterDaemon:
    """扫描 watched roots 的任务队列，全局串行消费。"""

    def __init__(self, roots: list[Path | str], poll_interval: float = 1.0) -> None:
        self.roots = [Path(r) for r in roots]
        self.poll_interval = poll_interval
        self._child: subprocess.Popen[bytes] | None = None
        self._child_task: dict[str, Any] | None = None
        self._log_fh: Any = None

    # ---------------- 主循环 ----------------
    def run_forever(self) -> None:
        self._recover_orphans()
        while True:
            for root in self.roots:
                tq.write_heartbeat(root, os.getpid())
            if self._stop_flagged():
                self._shutdown_gracefully()
                return
            if self._child is None:
                self._try_claim_and_spawn()
            else:
                self._poll_child()
            time.sleep(self.poll_interval)

    def _stop_flagged(self) -> bool:
        return any(tq.daemon_stop_flag(r).exists() for r in self.roots)

    def _shutdown_gracefully(self) -> None:
        """收完当前任务再退出（stop 语义：不杀正在写的进程，防止半章损坏）。"""
        for root in self.roots:
            tq.clear_daemon_stop_flag(root)
            tq.write_heartbeat(root, os.getpid())
        while self._child is not None:
            self._poll_child()
            if self._child is not None:
                time.sleep(self.poll_interval)
        for root in self.roots:
            tq.clear_daemon_stop_flag(root)

    def _recover_orphans(self) -> None:
        """启动时把遗留 running 任务标记 failed（上一次 daemon 崩溃的残留）。"""
        for root in self.roots:
            for project in self._projects_under(root):
                n = tq.interrupt_orphans(project)
                if n:
                    print(f"[daemon] 恢复：{project.name} 有 {n} 个中断任务已标记 failed")

    def _projects_under(self, root: Path) -> list[Path]:
        """root 下所有像小说项目的子目录（含 .state）。"""
        if not root.is_dir():
            return []
        return [p for p in sorted(root.iterdir()) if p.is_dir() and (p / ".state").is_dir()]

    # ---------------- 认领与执行 ----------------
    def _try_claim_and_spawn(self) -> None:
        if self._child is not None:
            return  # 全局串行：已有任务在执行，绝不认领第二个
        for root in self.roots:
            for project in self._projects_under(root):
                task = tq.claim_next(project, os.getpid())
                if task is None:
                    continue
                self._spawn(task)
                return

    def _build_cmd(self, task: dict) -> list[str]:
        cmd = [sys.executable, "-m", "agent.cli", task["command"]]
        project_dir = task["project_dir"]
        if task["command"] not in tq.NO_DIR_COMMANDS:
            cmd += ["--dir", project_dir]
        cmd += list(task.get("argv") or [])
        return cmd

    def _spawn(self, task: dict) -> None:
        project_dir = Path(task["project_dir"])
        log_path = tq.tasks_root(project_dir) / "logs" / f"{task['task_id']}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_fh = open(log_path, "ab", buffering=0)  # noqa: SIM115 - 生命周期与子进程绑定

        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        env["NOVEL_DATA_ROOT"] = str(project_dir.parent)
        env["NOVELAGENT_TASK_ID"] = task["task_id"]
        env.update(task.get("env_extra") or {})

        popen_kwargs: dict[str, Any] = {}
        if os.name == "nt":
            # CREATE_NEW_PROCESS_GROUP：进程树可整体管理（taskkill /T /F）；
            # CREATE_NO_WINDOW：daemon 无控制台，控制台型子进程（python.exe）
            # 会被系统自动分配一个可见控制台窗口——Web 自动写作时弹出 python
            # 黑窗（2026-09-07 用户反馈），必须显式压制。
            popen_kwargs["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            )

        try:
            proc = subprocess.Popen(
                self._build_cmd(task),
                cwd=str(REPO_ROOT),
                env=env,
                stdout=self._log_fh,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                **popen_kwargs,
            )
        except Exception as e:  # noqa: BLE001 - 启动失败 = 任务失败，不阻塞后续
            print(f"[daemon] 任务 {task['task_id']} 启动失败：{e}")
            tq.finalize_task(project_dir, task["task_id"], tq.STATUS_FAILED, note=f"spawn 失败：{e}")
            if self._log_fh:
                self._log_fh.close()
                self._log_fh = None
            return

        self._child = proc
        self._child_task = task
        tq.update_task(project_dir, task["task_id"], pid=proc.pid)
        print(f"[daemon] 执行任务 {task['task_id']}（{task['command']}，pid={proc.pid}）")

    def _poll_child(self) -> None:
        assert self._child is not None and self._child_task is not None
        task = self._child_task
        project_dir = Path(task["project_dir"])

        # 停止请求：杀进程树（轮询任务文件，CLI/Web 写入的 stop_requested）
        current = tq.get_task(project_dir, task["task_id"]) or {}
        if current.get("stop_requested") and self._child.returncode is None:
            print(f"[daemon] 收到停止请求：{task['task_id']}，终止进程树 pid={self._child.pid}")
            kill_process_tree_pid(self._child.pid)

        rc = self._child.poll()
        if rc is None:
            return
        self._child = None
        self._child_task = None
        if self._log_fh:
            self._log_fh.close()
            self._log_fh = None

        # rc 与 stop 标记双查：taskkill 后 rc 为非零，需区分「失败」与「被停止」
        was_stopped = bool(current.get("stop_requested"))
        if was_stopped:
            status = tq.STATUS_STOPPED
        elif rc == 0:
            status = tq.STATUS_DONE
        else:
            status = tq.STATUS_FAILED
        tq.finalize_task(project_dir, task["task_id"], status, exit_code=rc)
        print(f"[daemon] 任务 {task['task_id']} 结束：{status}（exit={rc}）")


# ---------------------------------------------------------------- 自动拉起
def ensure_daemon(roots: list[Path | str]) -> bool:
    """确保某个 watched root 上有活着的 daemon；没有则后台拉起。

    Returns:
        True 表示 daemon 已在运行或成功拉起。
    """
    roots = [Path(r) for r in roots]
    if any(tq.heartbeat_alive(r) for r in roots):
        return True
    args = [sys.executable, "-m", "agent.daemon"]
    for r in roots:
        args += ["--root", str(r)]
    kwargs: dict[str, Any] = {
        "cwd": str(REPO_ROOT),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_NO_WINDOW  # 双保险：任何路径拉起 daemon 都不弹窗
        )
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(args, **kwargs)
    except Exception:  # noqa: BLE001
        return False
    # 给 daemon 一点启动时间，再查一次心跳（不阻塞太久）
    for _ in range(10):
        time.sleep(0.3)
        if any(tq.heartbeat_alive(r) for r in roots):
            return True
    return False
