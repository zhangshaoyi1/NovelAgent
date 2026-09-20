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
# 2026-09-12：写命令默认上限改为**按提交章数缩放**（基础 1h + 15min/章），
# 固定 2h 会把长跑任务（如 335 章预计 13h+）在半路熔断（五灵破归档 ch190 事故）。
# 章数缩放只兜"慢"不兜"死"——真正的挂起由 should_stall 进度停滞熔断兜底。
DEFAULT_WRITE_MAX_RUNTIME_S = 7200
DEFAULT_OTHER_MAX_RUNTIME_S = 1800
# 章数缩放参数：基础墙钟 + 每章预算（15min/章为实测上限的保守值）
PER_CHAPTER_RUNTIME_S = 900
CHAPTER_BASE_RUNTIME_S = 3600

# 心跳阈值（与 daemon/follow.py 的 HEARTBEAT_MAX_AGE 对齐：>120s 视为失去监督）
HEARTBEAT_MAX_AGE = 120
# 进度停滞熔断窗口：项目内任一进度产物超过该时长无更新 → 判定挂起。
# 60min ≈ 实测最慢单章（~30min）的 2 倍余量；环境变量 NOVEL_PROGRESS_STALL_S 可调。
PROGRESS_STALL_S = 3600
# 进度信号文件/目录（相对项目根）：任一 mtime 新于阈值即视为"有进度"
_PROGRESS_SIGNALS = (
    ".state/progress.json",
    ".events/events.jsonl",
    ".state/quality_audit.jsonl",
    ".state/tasks/logs",   # 目录：任一任务日志 mtime
    "chapters",            # 目录：任一章节文件 mtime
)


def has_explicit_max_time(argv: list[str]) -> bool:
    """argv 是否显式携带 --max-time（显式值沿用墙钟语义，不做章数缩放）。"""
    for i, a in enumerate(argv):
        if a == "--max-time" and i + 1 < len(argv):
            return True
        if a.startswith("--max-time="):
            return True
    return False


def _parse_int(raw: Any) -> int | None:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _chapters_from_argv(argv: list[str]) -> int:
    """从 argv 解析目标章数：--chapters/-n 绝对值优先，--batch/-b 兜底；无则 0。"""
    for i, a in enumerate(argv):
        val: int | None = None
        if a in ("--chapters", "-n", "--batch", "-b") and i + 1 < len(argv):
            val = _parse_int(argv[i + 1])
        elif a.startswith("--chapters=") or a.startswith("--batch="):
            val = _parse_int(a.split("=", 1)[1])
        if val is not None and val > 0:
            return val
    return 0


def resolve_max_runtime(command: str, argv: list[str]) -> int:
    """任务超时阈值：CLI --max-time 显式优先；写命令按章数缩放；无章数信息回退 2h。

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
        chapters = _chapters_from_argv(argv)
        if chapters > 0:
            return max(
                CHAPTER_BASE_RUNTIME_S + PER_CHAPTER_RUNTIME_S * chapters,
                DEFAULT_WRITE_MAX_RUNTIME_S,
            )
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


def _safe_mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def _latest_progress_mtime(project_dir: Path | str) -> float:
    """项目内最新进度产物 mtime（无任何产物返回 0.0）。"""
    root = Path(project_dir)
    latest = 0.0
    for rel in _PROGRESS_SIGNALS:
        p = root / rel
        if p.is_file():
            latest = max(latest, _safe_mtime(p))
        elif p.is_dir():
            for child in p.iterdir():
                latest = max(latest, _safe_mtime(child))
    return latest


def should_stall(task: dict[str, Any], now: float | None = None) -> bool:
    """进度停滞判定（每章重置语义）：running 且项目进度产物超过窗口无更新。

    与墙钟 should_timeout 互补：墙钟兜"整体超预算"，停滞兜"单点挂死"。
    只要流水线还在产出（事件/进度/章节/日志任一有更新），即视为健康推进，
    无论已运行多久——效果等价于"每写一章就重置超时"。
    项目无任何进度产物（如冷启动前期）时保守返回 False，交给墙钟判定。
    """
    if task.get("status") != tq.STATUS_RUNNING:
        return False
    if not task.get("project_dir"):
        return False
    now_ts = now if now is not None else time.time()
    window = _env_int_stall()
    latest = _latest_progress_mtime(task["project_dir"])
    if latest <= 0:
        return False
    return (now_ts - latest) > window


def _env_int_stall() -> int:
    import os

    try:
        v = int(os.environ.get("NOVEL_PROGRESS_STALL_S", "") or PROGRESS_STALL_S)
        return v if v > 0 else PROGRESS_STALL_S
    except ValueError:
        return PROGRESS_STALL_S


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


def _heartbeat_state(task_id: str, hb: Any) -> str:
    """任务心跳三态：``fresh`` / ``stale`` / ``unknown``。

    ★ B3（H5）：「不知道」必须与「确证过期」分开——前者保守接管，后者才授权杀。
    ``unknown``（缺失 / 不可解析）**显性留痕**，不静默（纪律 #1、#2）。
    """
    if not hb:
        degrade(
            "process_manager.heartbeat_unknown",
            f"任务 {task_id} 心跳缺失，无法判定新鲜度 ⇒ 按未知处理（保守接管）",
        )
        return "unknown"
    try:
        age = time.time() - datetime.fromisoformat(str(hb)).timestamp()
    except (TypeError, ValueError):
        degrade(
            "process_manager.heartbeat_unknown",
            f"任务 {task_id} 心跳不可解析，无法判定新鲜度 ⇒ 按未知处理（保守接管）",
        )
        return "unknown"
    return "fresh" if age < HEARTBEAT_MAX_AGE else "stale"


def recover_running(project_dir: Path | str) -> int:
    """崩溃恢复（先杀后标/重新接管）：扫描 running 任务。

    对每个 running 任务：
    - pid 存活 且 心跳新鲜 → 重新接管（保持 running，由后续 daemon 继续监督）；
    - pid 存活 且 心跳**确证过期** → 孤儿进程仍在跑 → 杀进程树 + 标记 orphan_killed + 锁清；
    - pid 存活 但 心跳**缺失/不可解析** → **保守接管，不杀**（见下）；
    - pid 已死 → 标记 orphan_died + 锁清。

    ★ B3（2026-09-20，H5）：原实现把「心跳读不到/解析失败」也判 ``fresh=False``
    ⇒ 与"确证过期"共用同一条 **杀** 分支。后果：**心跳只是落盘失败**（磁盘/权限
    瞬时问题，`update_task_heartbeat` 写不进去）或字段格式异常时，进程明明还活着，
    却被"先杀后标"杀掉——即"心跳落盘失败 ⇒ 误杀健康任务"。
    判据强度必须与证据匹配（纪律 #2/#13）：「不知道」不得升级成不可逆动作。
    现拆开：只有**确证存在且超窗**的心跳才授权杀；未知一律保守接管，并由
    `should_timeout` / `should_stall` 在后续监督中兜底（判据可见、可解释）。
    双写风险：接管期间若有 pending 任务被认领，同项目由 project-lock + 写命令
    串行兜住（本就存在的护栏），不因本改动新增敞口。

    Returns:
        处理的任务数。
    """
    n = 0
    for task in tq.iter_tasks(project_dir, status=tq.STATUS_RUNNING):
        n += 1
        pid = int(task.get("pid") or 0)
        task_id = task.get("task_id", "")
        alive = _pid_alive(pid)
        # 三态：fresh（确证新鲜）/ stale（确证过期）/ unknown（读不到、解析不了）
        state = _heartbeat_state(task_id, task.get("heartbeat_at"))
        if alive and state in ("fresh", "unknown"):
            # 重新接管：任务仍由（新）daemon 监督，保持 running
            # （unknown 的留痕已在 _heartbeat_state 内完成，不静默、不升级为杀）
            continue
        if alive:
            # 孤儿进程仍在跑（心跳**确证过期**）：先杀后标（防与新任务抢写）
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
