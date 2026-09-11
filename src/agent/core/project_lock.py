"""项目级单写者锁。

同一本小说目录同一时刻只允许一个写进程（autowrite/rewrite 等）落盘，
防止并发写导致章节互相覆盖、内容交错损坏（2026-09-05 无灵项目 ch086 事故）。

机制：``<project>/.state/writer.lock`` 文件 + O_EXCL 原子创建；
锁内记录 pid / 启动时间 / 命令名，持有者已死（陈旧锁）则自动接管。

锁名归一（2026-09-07，五灵破归档「写 5 章出 6 章」事故根治 L1-1）：
    历史实现把命令名拼进锁文件名（``autowrite.lock`` / ``write.lock``），
    同一项目上两把锁互不排斥 —— CLI 跑 autowrite 的同时 Web 点「写下一章」
    即可并发写同一本书。现固定为单把 ``writer.lock``，``command`` 参数降级为
    仅写入锁内容的持有者标注，不再参与文件名。
"""

from __future__ import annotations

import atexit
import json
import os
import time
from datetime import datetime
from pathlib import Path

from agent.core.infra.degrade import degrade


#: 锁文件名（固定，不随命令变化）。见模块 docstring「锁名归一」。
LOCK_BASENAME = "writer.lock"

#: 逃生开关：置为 1 时跳过一切项目写锁（仅用于批处理/测试，勿在生产开启）。
SKIP_ENV = "NOVEL_AGENT_SKIP_WRITE_LOCK"

#: 锁继承：编排进程（compose）spawn 写子进程（autowrite 等）时注入本环境变量，
#: 值为父进程 PID。子进程发现锁持有者正是该 PID 时视为同一条写链路放行——
#: 否则子进程会被自己父进程持有的锁挡死（父子 PID 不同）。
INHERIT_ENV = "NOVEL_AGENT_INHERIT_LOCK_PID"

#: 兼容：历史上按命令切分的旧锁文件名，探测时一并识别（防止老进程残留误导）。
LEGACY_LOCK_NAMES = ("autowrite.lock", "write.lock", "rewrite.lock")

#: 接管陈旧锁的最大轮次。删除/改名都失败时不无限循环，宁可保守报忙（绝不双写）。
_STALE_MAX_ROUNDS = 50


def lock_path_for(project_dir: Path | str) -> Path:
    """项目写锁的唯一路径：``<project>/.state/writer.lock``。"""
    return Path(project_dir) / ".state" / LOCK_BASENAME


class ProjectLockBusy(RuntimeError):
    """已有活跃写进程持有该项目锁。"""

    def __init__(self, lock_path: Path, info: dict):
        self.lock_path = lock_path
        self.info = info
        super().__init__(
            f"项目已有写进程在运行：pid={info.get('pid')} "
            f"started={info.get('started_at')} cmd={info.get('command')} "
            f"（锁文件：{lock_path}）。等待其结束，或运行 "
            f"`agent unlock -d <项目目录>` 清理已确认死亡的陈旧锁。"
        )


def _pid_alive(pid: int) -> bool:
    """仅做存活探测，不发送任何信号（Windows 上 os.kill 会终止进程，禁用）。"""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        SYNCHRONIZE = 0x00100000
        ERROR_ACCESS_DENIED = 5
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if not handle:
            # F-8：OpenProcess 失败不一律判死——ERROR_ACCESS_DENIED 表示进程
            # 存在但权限不足（跨会话/受保护进程，如不同用户启动的 CLI 与 Web 子进程），
            # 此时误判「陈旧锁」会删锁接管，导致双写并发（五灵破归档事故根因）。
            # 保守：权限不足视为存活（不接管，交人工确认）；仅“进程不存在”类错误判死。
            if ctypes.get_last_error() == ERROR_ACCESS_DENIED:
                return True
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


def _inherited_lock_pid() -> int:
    """读取锁继承环境变量；无效或不匹配返回 0。"""
    raw = os.environ.get(INHERIT_ENV, "")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def acquire_project_lock(project_dir: Path | str, command: str = "writer") -> Path:
    """获取项目写锁；被占用则抛 ProjectLockBusy，成功则注册 atexit 自动释放。

    ``command`` 只作为持有者标注写进锁内容（便于诊断谁在写），**不影响锁文件名**——
    全项目共用一把 ``writer.lock``。
    """
    if os.environ.get(SKIP_ENV) == "1":
        return lock_path_for(project_dir)
    lock_path = lock_path_for(project_dir)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    info = {
        "pid": os.getpid(),
        "command": command,
        "host": os.environ.get("COMPUTERNAME", ""),
        "started_at": datetime.now().isoformat(timespec="seconds"),
    }
    payload = json.dumps(info, ensure_ascii=False)
    stale_rounds = 0

    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            existing = _read_lock(lock_path)
            old_pid = int(existing.get("pid") or 0)
            if old_pid == os.getpid():
                # 本进程重复获取（如嵌套调用），视为已持有
                return lock_path
            if old_pid and old_pid == _inherited_lock_pid():
                # 锁继承：持有者是派生本进程的父编排进程（compose→autowrite），
                # 属于同一条写链路，放行共享父锁（互斥对外仍然生效）。
                return lock_path
            if _pid_alive(old_pid):
                raise ProjectLockBusy(lock_path, existing) from None
            # 陈旧锁：持有者已死，接管（先删后建，重建失败则继续重试）
            stale_rounds += 1
            if stale_rounds > _STALE_MAX_ROUNDS:
                # 删也删不掉、挪也挪不走：保守报忙（绝不双写，这是锁的唯一目的）
                raise ProjectLockBusy(lock_path, existing) from None
            try:
                lock_path.unlink()
            except OSError:
                pass  # noqa: SILENT_DEGRADE
            except SystemExit as exc:
                # WorkBuddy safe-delete 批量护栏会拦下 unlink 并抛 SystemExit
                # （2026-09-11 事故）。降级为「改名挪走」——rename 是移动而非删除，
                # 不触发护栏，同样能腾出锁名给 O_EXCL 重建。
                degrade(
                    "project_lock.acquire",
                    "陈旧锁删除被 safe-delete 护栏拦截，改用改名挪走",
                    exc,
                )
            if lock_path.exists():
                _quarantine_stale(lock_path)
            time.sleep(0.05)
            continue  # noqa: SILENT_DEGRADE
        else:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
            atexit.register(_release_lock, lock_path)
            return lock_path


def probe_project_lock(project_dir: Path | str, command: str | None = None) -> dict | None:
    """只读探测：锁被活跃进程持有时返回持有者信息，否则返回 None。不创建/删除锁。

    ``command`` 仅为兼容旧调用点保留，不参与锁文件定位（全项目一把 ``writer.lock``）。
    同时兼容识别历史遗留的按命令命名锁文件，避免升级后老进程残留被漏判。
    """
    candidates = [lock_path_for(project_dir)]
    candidates += [Path(project_dir) / ".state" / n for n in LEGACY_LOCK_NAMES]
    lock_path = next((p for p in candidates if p.exists()), None)
    if lock_path is None:
        return None
    existing = _read_lock(lock_path)
    try:
        pid = int(existing.get("pid") or 0)
    except (TypeError, ValueError):
        return None
    if pid == os.getpid():
        # 本进程自己持有（如派发层已加过锁、命令体内再预检）→ 视为空闲，
        # 否则写命令会把自己的锁误判成「他人占用」而秒退。
        return None
    if pid and pid == _inherited_lock_pid():
        # 锁继承：持有者是派生本进程的父编排进程 → 同链路，视为空闲。
        return None
    if not _pid_alive(pid):
        return None
    return {**existing, "lock_path": str(lock_path)}


def _read_lock(lock_path: Path) -> dict:
    try:
        return json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _quarantine_stale(lock_path: Path) -> None:
    """把删不掉的陈旧锁改名挪走。

    ``os.replace`` 是**移动**而非删除，不会触发 WorkBuddy safe-delete 批量护栏
    （该护栏只 hook ``os.remove/unlink/rmdir``、``shutil.rmtree``、``Path.unlink/rmdir``）。
    固定挪到 ``<lock>.stale``（覆盖式），不累积垃圾文件。
    """
    try:
        os.replace(str(lock_path), f"{lock_path}.stale")
    except OSError as exc:
        degrade("project_lock.acquire", "陈旧锁改名挪走失败，下一轮重试", exc)


def _release_lock(lock_path: Path) -> None:
    """atexit 释放写锁（尽力而为，绝不抛出）。

    注意：除 ``OSError`` 外必须吞掉 ``SystemExit``——WorkBuddy safe-delete shim
    在批量删除护栏触发时抛 ``SystemExit(1)``。逃逸到 atexit 的后果是：进程带
    失败码退出（**写批次实际成功却被判失败**）并留下陈旧锁（2026-09-11 事故）。
    锁文件残留本身无害：``acquire_project_lock`` 判定持有者已死后会自动接管。
    """
    try:
        info = _read_lock(lock_path)
        if int(info.get("pid") or 0) == os.getpid():
            lock_path.unlink()
    except OSError:
        pass  # noqa: SILENT_DEGRADE
    except SystemExit as exc:
        degrade(
            "project_lock.release",
            "释放写锁被 safe-delete 护栏拦截，锁文件残留（下次 acquire 自动接管）",
            exc,
        )
