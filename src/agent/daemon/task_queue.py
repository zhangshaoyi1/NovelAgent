"""落盘任务队列（D1-1）：writer daemon 的任务持久化层。

队列布局（``<project>/.state/tasks/``）::

    pending/<task_id>.json   排队中（追加即提交，tmp+rename 原子写入）
    running/<task_id>.json   执行中（daemon 认领时 rename 移入）
    done/<task_id>.json      终态：status = done | failed | stopped
    logs/<task_id>.log       daemon 捕获的任务 stdout/stderr

设计约束：
- **无守护进程也能提交**：submit 只做原子文件写入，daemon 未起时任务安全
  排队（``ensure_daemon`` 负责自动拉起）。
- **原子认领**：``claim_next`` 用 ``os.replace`` 把 pending 移到 running，
  rename 在同一分区内是原子操作，无需分布式锁。
- **停止语义**：pending 状态的 stop = 直接取消（移入 done/stopped）；
  running 状态的 stop = 在任务文件里置 ``stop_requested=true``，daemon
  轮询到后杀进程树。 stop 请求同样走文件，CLI / Web / daemon 三方共享。
- **崩溃恢复**：daemon 启动时把遗留的 running 任务标记为 failed
  （events.jsonl 有证据链，任务本身不幂等，不自动重跑）。

任务 schema（json）::

    task_id       排序友好 ID：<YYYYmmddHHMMSS>-<hex6>
    command       CLI 命令名（如 autowrite / write / compose）
    argv          命令参数列表（不含 --dir，daemon 按任务注入）
    project_dir   项目绝对路径
    submitted_by  cli | web
    submitted_at  ISO 时间
    env_extra     注入子进程的额外环境变量（如 NOVEL_MODEL_PROFILE）
    status        queued | running | done | failed | stopped
    stop_requested bool
    pid / started_at / finished_at / exit_code / note
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

#: 不接受 --dir 的全局工具命令（原 web.runner 定义迁移至此，单一真相源）。
NO_DIR_COMMANDS = {
    "export-skill",
    "genre-info",
    "help",
    "list-genres",
    "load-skill",
    "version",
}

#: 任务状态常量
STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_STOPPED = "stopped"

TERMINAL_STATUSES = (STATUS_DONE, STATUS_FAILED, STATUS_STOPPED)

#: daemon 心跳新鲜度阈值（秒）：超过则视为 daemon 未在运行
HEARTBEAT_MAX_AGE = 20


def tasks_root(project_dir: Path | str) -> Path:
    return Path(project_dir) / ".state" / "tasks"


def _ensure_dirs(project_dir: Path) -> None:
    for sub in ("pending", "running", "done", "logs"):
        (tasks_root(project_dir) / sub).mkdir(parents=True, exist_ok=True)


def _atomic_write_json(path: Path, data: dict) -> None:
    """同目录 tmp + os.replace 原子写入，避免读到半截 json。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _find_task_file(root: Path, task_id: str) -> Path | None:
    """在 pending/running/done 中定位任务文件（pending 优先）。"""
    for sub in ("pending", "running", "done"):
        p = root / sub / f"{task_id}.json"
        if p.exists():
            return p
    return None


def new_task_id() -> str:
    """排序友好任务 ID：时间前缀（同秒内按文件名二次排序）+ 随机后缀。"""
    return f"{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"


# ---------------------------------------------------------------- 提交 / 查询
def submit_task(
    project_dir: Path | str,
    command: str,
    argv: list[str] | None = None,
    submitted_by: str = "cli",
    env_extra: dict[str, str] | None = None,
) -> dict:
    """提交一个任务到项目队列（追加即提交），返回任务 dict。"""
    project_dir = Path(project_dir)
    _ensure_dirs(project_dir)
    task = {
        "task_id": new_task_id(),
        "command": command,
        "argv": list(argv or []),
        "project_dir": str(project_dir.resolve()),
        "submitted_by": submitted_by,
        "submitted_at": datetime.now().isoformat(timespec="seconds"),
        "env_extra": dict(env_extra or {}),
        "status": STATUS_QUEUED,
        "stop_requested": False,
    }
    path = tasks_root(project_dir) / "pending" / f"{task['task_id']}.json"
    _atomic_write_json(path, task)
    return task


_STATUS_TO_SUBDIR = {
    STATUS_QUEUED: "pending",
    STATUS_RUNNING: "running",
    STATUS_DONE: "done",
    STATUS_FAILED: "done",
    STATUS_STOPPED: "done",
}


def iter_tasks(project_dir: Path | str, status: str | None = None) -> Iterator[dict]:
    """按状态枚举任务（status=None 时遍历 pending/running/done 全部）。

    状态与目录的映射：queued→pending/；running→running/；
    done/failed/stopped→done/（再按任务内 status 字段过滤）。
    """
    root = tasks_root(Path(project_dir))
    if status is None:
        subdirs = ("pending", "running", "done")
    else:
        subdirs = (_STATUS_TO_SUBDIR.get(status, status),)
    for sub in subdirs:
        d = root / sub
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.json")):
            task = _read_json(p)
            if task and (status is None or task.get("status") == status):
                yield task


def get_task(project_dir: Path | str, task_id: str) -> dict | None:
    """按 ID 查任务（在 pending/running/done 三处查找）。"""
    root = tasks_root(Path(project_dir))
    for sub in ("pending", "running", "done"):
        p = root / sub / f"{task_id}.json"
        if p.exists():
            task = _read_json(p)
            if task:
                return task
    return None


# ---------------------------------------------------------------- 认领 / 终态
def claim_next(project_dir: Path | str, daemon_pid: int) -> dict | None:
    """认领最早的排队任务：pending → running（原子 rename），返回任务 dict。"""
    root = tasks_root(Path(project_dir))
    pending_dir = root / "pending"
    if not pending_dir.is_dir():
        return None
    candidates = sorted(pending_dir.glob("*.json"))
    for path in candidates:
        task = _read_json(path)
        if not task:
            continue
        target = root / "running" / path.name
        try:
            os.replace(path, target)
        except OSError:
            continue  # noqa: SILENT_DEGRADE - 被并发取消等，跳过
        task["status"] = STATUS_RUNNING
        task["pid"] = daemon_pid
        task["started_at"] = datetime.now().isoformat(timespec="seconds")
        _atomic_write_json(target, task)
        return task
    return None


def update_task(project_dir: Path | str, task_id: str, **fields: Any) -> dict | None:
    """就地更新任务字段（仅 running 状态由 daemon 使用）。"""
    path = tasks_root(Path(project_dir)) / "running" / f"{task_id}.json"
    if not path.exists():
        return None
    task = _read_json(path)
    if not task:
        return None
    task.update(fields)
    _atomic_write_json(path, task)
    return task


def finalize_task(
    project_dir: Path | str,
    task_id: str,
    status: str,
    exit_code: int | None = None,
    note: str = "",
) -> dict | None:
    """把任务归档为终态（pending 或 running → done/ 目录，status 字段区分）。"""
    assert status in TERMINAL_STATUSES, f"非法终态：{status}"
    root = tasks_root(Path(project_dir))
    src = _find_task_file(root, task_id)
    if src is None:
        return None
    task = _read_json(src)
    if not task:
        return None
    task["status"] = status
    task["exit_code"] = exit_code
    task["finished_at"] = datetime.now().isoformat(timespec="seconds")
    if note:
        task["note"] = note
    _atomic_write_json(root / "done" / src.name, task)
    try:
        src.unlink()
    except OSError:
        pass  # noqa: SILENT_DEGRADE
    return task


# ---------------------------------------------------------------- 停止
def request_stop(project_dir: Path | str, task_id: str) -> str | None:
    """请求停止任务。

    Returns:
        "cancelled"  任务还在排队，已直接取消
        "signaled"   任务运行中，已置 stop_requested（daemon 轮询到后杀进程树）
        None         任务不存在或已是终态
    """
    root = tasks_root(Path(project_dir))
    path = _find_task_file(root, task_id)
    if path is None:
        return None
    task = _read_json(path)
    if not task or task.get("status") in TERMINAL_STATUSES:
        return None
    if path.parent.name == "pending":
        finalize_task(project_dir, task_id, STATUS_STOPPED, note="排队中被取消")
        return "cancelled"
    update_task(project_dir, task_id, stop_requested=True)
    return "signaled"


# ---------------------------------------------------------------- 崩溃恢复
def interrupt_orphans(project_dir: Path | str, note: str = "daemon 重启，检测到中断任务") -> int:
    """daemon 启动时调用：把遗留的 running 任务标记为 failed。返回处理数。"""
    root = tasks_root(Path(project_dir))
    running_dir = root / "running"
    if not running_dir.is_dir():
        return 0
    n = 0
    for path in sorted(running_dir.glob("*.json")):
        task = _read_json(path)
        if task:
            finalize_task(
                project_dir,
                task["task_id"],
                STATUS_FAILED,
                exit_code=None,
                note=note,
            )
            n += 1
    return n


# ---------------------------------------------------------------- daemon 心跳与生命周期
def heartbeat_path(root: Path | str) -> Path:
    return Path(root) / ".daemon" / "heartbeat.json"


def write_heartbeat(root: Path | str, pid: int) -> None:
    _atomic_write_json(
        heartbeat_path(root),
        {"pid": pid, "ts": time.time(), "updated_at": datetime.now().isoformat(timespec="seconds")},
    )


def read_heartbeat(root: Path | str) -> dict | None:
    data = _read_json(heartbeat_path(root))
    return data or None


def heartbeat_alive(root: Path | str, max_age: int = HEARTBEAT_MAX_AGE) -> bool:
    """心跳文件存在且足够新 → daemon 在运行。"""
    data = read_heartbeat(root)
    if not data:
        return False
    try:
        return (time.time() - float(data.get("ts") or 0)) < max_age
    except (TypeError, ValueError):
        return False


def daemon_stop_flag(root: Path | str) -> Path:
    return Path(root) / ".daemon" / "stop.flag"


def request_daemon_stop(root: Path | str) -> None:
    """请求 daemon 在完成当前任务后退出（写标志文件）。"""
    daemon_stop_flag(root).parent.mkdir(parents=True, exist_ok=True)
    daemon_stop_flag(root).write_text(
        datetime.now().isoformat(timespec="seconds"), encoding="utf-8"
    )


def clear_daemon_stop_flag(root: Path | str) -> None:
    try:
        daemon_stop_flag(root).unlink()
    except OSError:
        pass  # noqa: SILENT_DEGRADE
