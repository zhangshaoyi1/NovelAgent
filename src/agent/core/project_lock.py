"""项目级单写者锁。

同一本小说目录同一时刻只允许一个写进程（autowrite/rewrite 等）落盘，
防止并发写导致章节互相覆盖、内容交错损坏（2026-09-05 无灵项目 ch086 事故）。

机制：``<project>/.state/<command>.lock`` 文件 + O_EXCL 原子创建；
锁内记录 pid / 启动时间，持有者已死（陈旧锁）则自动接管。
"""

from __future__ import annotations

import atexit
import json
import os
import time
from datetime import datetime
from pathlib import Path


class ProjectLockBusy(RuntimeError):
    """已有活跃写进程持有该项目锁。"""

    def __init__(self, lock_path: Path, info: dict):
        self.lock_path = lock_path
        self.info = info
        super().__init__(
            f"项目已有写进程在运行：pid={info.get('pid')} "
            f"started={info.get('started_at')} cmd={info.get('command')} "
            f"（锁文件：{lock_path}）。确认该进程已结束后再启动，或手动删除锁文件。"
        )


def _pid_alive(pid: int) -> bool:
    """仅做存活探测，不发送任何信号（Windows 上 os.kill 会终止进程，禁用）。"""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        SYNCHRONIZE = 0x00100000
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if not handle:
            return False
        try:
            # WAIT_TIMEOUT(0x102) 表示仍在运行
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


def acquire_project_lock(project_dir: Path | str, command: str = "autowrite") -> Path:
    """获取项目写锁；被占用则抛 ProjectLockBusy，成功则注册 atexit 自动释放。"""
    lock_path = Path(project_dir) / ".state" / f"{command}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    info = {
        "pid": os.getpid(),
        "command": command,
        "host": os.environ.get("COMPUTERNAME", ""),
        "started_at": datetime.now().isoformat(timespec="seconds"),
    }
    payload = json.dumps(info, ensure_ascii=False)

    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            existing = _read_lock(lock_path)
            old_pid = int(existing.get("pid") or 0)
            if old_pid == os.getpid():
                # 本进程重复获取（如嵌套调用），视为已持有
                return lock_path
            if _pid_alive(old_pid):
                raise ProjectLockBusy(lock_path, existing) from None
            # 陈旧锁：持有者已死，接管（先删后建，重建失败则继续重试）
            try:
                lock_path.unlink()
            except OSError:
                pass
            time.sleep(0.05)
            continue
        else:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
            atexit.register(_release_lock, lock_path)
            return lock_path


def _read_lock(lock_path: Path) -> dict:
    try:
        return json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _release_lock(lock_path: Path) -> None:
    try:
        info = _read_lock(lock_path)
        if int(info.get("pid") or 0) == os.getpid():
            lock_path.unlink()
    except OSError:
        pass
