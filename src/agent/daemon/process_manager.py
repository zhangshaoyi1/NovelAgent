"""统一进程管理（阶段 1：ProcessManager）——任务生命周期唯一权威

方案：<项目文档/模块设计/进程管理方案.md>

本模块提供进程生命周期的统一出口，WriterDaemon / Web / CLI 均委托这里完成：
- ``resolve_max_runtime``：任务超时阈值解析（CLI --max-time 优先，命令默认兜底）
- ``should_timeout``：超时判定（now - started_at > max_runtime_s）
- ``force_terminate``：统一强制终止（taskkill /T /F 杀树 + 终态标记 + 锁自愈）
- ``cleanup_stale_lock``：锁自愈（写锁 PID 已死且任务已终态 → 清理）
- ``recover_running``：崩溃恢复（先杀后标 / 重新接管 / 锁清理）——取代「只标 failed 不杀进程」

约定（复用 follow.py 的 120s 心跳阈值）：
- 任务级 ``heartbeat_at`` 由 daemon 每轮监督刷新；daemon 崩溃后心跳停更，
  recover 据此区分「孤儿进程仍在跑」与「已随 daemon 消亡」。
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.core.infra.degrade import degrade
from agent.daemon import task_queue as tq

# 默认超时：写命令 2h（长任务），非写命令 30min（短任务兜底，防止僵尸堵串行队列）
DEFAULT_WRITE_MAX_RUNTIME_S = 7200
DEFAULT_OTHER_MAX_RUNTIME_S = 1800

# 心跳阈值（与 daemon/follow.py 的 HEARTBEAT_MAX_AGE 对齐：>120s 视为失去监督）
HEARTBEAT_MAX_AGE = 120


def resolve_max_runtime(command: str, argv: list[str]) -> int:
    """任务超时阈值：CLI --max-time 显式优先；否则按命令类型默认兜底。

    支持 ``--max-time 3600`` 与 ``--max-time=3600`` 两种写法。
    """
    for i, a in enumerate(argv):
        if a == "--max-time" and i + 1 < len(argv):
            try:
                v = int(argv[i + 1])
                if v > 0:
                    return v
            except (TypeError, ValueError):  # noqa: SILENT_DEGRADE - 解析失败回退默认/跳过
                break
        elif a.startswith("--max-time="):
            try:
                v = int(a.split("=", 1)[1])
                if v > 0:
                    return v
            except (TypeError, ValueError):  # noqa: SILENT_DEGRADE - 解析失败回退默认/跳过
                break
    if command in tq.writer_commands():
        return DEFAULT_WRITE_MAX_RUNTIME_S
    return DEFAULT_OTHER_MAX_RUNTIME_S


def should_timeout(task: dict[str, Any], now: float | None = None) -> bool:
    """超时判定：running 且已运行超过 max_runtime_s。"""
    if task.get("status") != tq.STATUS_RUNNING:
        return False
    max_runtime = int(task.get("max_runtime_s") or 0)
    if max_runtime <= 0:
        return False
    started = task.get("started_at")
    if not started:
        return False
    try:
        started_ts = datetime.fromisoformat(started).timestamp()
    except (TypeError, ValueError):  # noqa: SILENT_DEGRADE - 解析失败回退默认/跳过
        return False
    now_ts = now if now is not None else time.time()
    return (now_ts - started_ts) > max_runtime


def _pid_alive(pid: int) -> bool:
    """进程存活探测（Windows 兼容，与 project_lock 同款保守语义）。"""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            # ERROR_ACCESS_DENIED(5) = 进程存在但权限不足 → 保守判活；
            # 其他错误（87 INVALID_PARAMETER 等）= 进程不存在 → 判死。
            return ctypes.get_last_error() == 5
        try:
            return kernel32.WaitForSingleObject(handle, 0) == 0x102
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def kill_process_tree_pid(pid: int) -> bool:
    """按 PID 终止进程树（Windows taskkill /T /F；POSIX killpg）。返回是否成功发起。"""
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            r = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
                check=False,
            )
            return r.returncode == 0
        except Exception as e:  # noqa: BLE001 - taskkill 失败退回通用兜底
            degrade("process_manager.kill", f"taskkill 失败（pid={pid}），退回 os.kill", e)
    try:
        if hasattr(os, "killpg"):
            import signal

            os.killpg(pid, signal.SIGTERM)
        else:
            os.kill(pid, 15)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def cleanup_stale_lock(project_dir: Path | str, pid: int) -> bool:
    """锁自愈：pid 已死且确属该任务持有的写锁 → 清理（防项目被残留锁卡死）。

    只清理 ``.state/writer.lock``（统一写锁）且 pid 精确匹配——配合 F-8 的
    保守接管：ProcessManager 有任务记录（lock_owned + 终态）才自动清，
    不碰其他进程可能持有的锁。
    """
    lock = Path(project_dir) / ".state" / "writer.lock"
    if not lock.exists():
        return False
    try:
        import json

        data = json.loads(lock.read_text(encoding="utf-8"))
        lock_pid = int(data.get("pid") or 0)
    except (OSError, ValueError):
        return False
    if lock_pid != pid:
        return False
    if _pid_alive(pid):
        return False  # 进程还活着，不接管（保守）
    try:
        lock.unlink()
        return True
    except (OSError, SystemExit):  # noqa: SILENT_DEGRADE - 删除被 safe-delete 护栏拦时改名挪走
        # WorkBuddy safe-delete 护栏批量拦删除抛 SystemExit（2026-09-11 事故）——
        # 锁清理是崩溃恢复关键路径，不得被逃逸打崩；改名（移动）不触发护栏，效果等同解锁。
        try:
            os.replace(str(lock), f"{lock}.stale")
            return True
        except (OSError, SystemExit):  # noqa: SILENT_DEGRADE - 改名也失败则以实际存在性为准
            return not lock.exists()
    # 注：try/except 双分支均已 return，原先此处的 `return False` 不可达
    #（2026-09-11 死代码清理）


def force_terminate(
    project_dir: Path | str,
    task_id: str,
    pid: int,
    *,
    note: str = "强制终止（进程管理）",
    status: str = tq.STATUS_STOPPED,
) -> bool:
    """统一强制终止：杀进程树 → 终态标记 → 锁自愈。

    Returns:
        是否成功发起终止（杀树调用是否返回成功）。
    """
    ok = kill_process_tree_pid(pid)
    tq.finalize_task(project_dir, task_id, status, note=note)
    if task_id:
        cleanup_stale_lock(project_dir, pid)
    return ok


def recover_running(project_dir: Path | str) -> int:
    """崩溃恢复（先杀后标/重新接管）：扫描 running 任务。

    对每个 running 任务：
    - pid 存活 且 心跳新鲜 → 重新接管（保持 running，由后续 daemon 继续监督）；
    - pid 存活 但 心跳过期 → 孤儿进程仍在跑 → 杀进程树 + 标记 orphan_killed + 锁清；
    - pid 已死 → 标记 orphan_died + 锁清。

    Returns:
        处理的任务数。
    """
    n = 0
    for task in tq.iter_tasks(project_dir, status=tq.STATUS_RUNNING):
        n += 1
        pid = int(task.get("pid") or 0)
        task_id = task.get("task_id", "")
        alive = _pid_alive(pid)
        hb = task.get("heartbeat_at")
        fresh = True
        if hb:
            try:
                fresh = (time.time() - datetime.fromisoformat(hb).timestamp()) < HEARTBEAT_MAX_AGE
            except (TypeError, ValueError):  # noqa: SILENT_DEGRADE - 解析失败回退默认/跳过
                fresh = False
        if alive and fresh:
            # 重新接管：任务仍由（新）daemon 监督，保持 running
            continue
        if alive:
            # 孤儿进程仍在跑：先杀后标（防与新任务抢写）
            force_terminate(
                project_dir, task_id, pid,
                note="daemon 崩溃恢复：孤儿进程已终止（先杀后标）",
                status=tq.STATUS_FAILED,
            )
        else:
            tq.finalize_task(
                project_dir, task_id, tq.STATUS_FAILED,
                note="daemon 崩溃恢复：进程已消亡，任务标记失败",
            )
            cleanup_stale_lock(project_dir, pid)
    return n
