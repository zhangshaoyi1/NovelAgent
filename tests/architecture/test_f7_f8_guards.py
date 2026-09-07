"""F-7/F-8 回归测试（五灵破归档"写 5 章出 6 章"事故加固）

- F-7：写章循环本地 wrote 兜底终止 + total_written 只增不回流
- F-8：锁 PID 探测 Windows 加固（ACCESS_DENIED 保守判活）+ 同 PID 幂等

详见《架构评审与待办事项.md》事故复盘（2026-09-07）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


# ---------------------------------------------------------------- F-8 锁
def test_pid_alive_semantics() -> None:
    """PID 存活探测：本进程存活、极大 PID 视为不存在、pid<=0 为死。"""
    from agent.core.project_lock import _pid_alive

    assert _pid_alive(os.getpid()) is True, "本进程应存活"
    assert _pid_alive(999_999_999) is False, "极大 PID 应判死（Windows OpenProcess 失败且非权限）"
    assert _pid_alive(0) is False
    assert _pid_alive(-1) is False


def test_acquire_lock_same_pid_idempotent(tmp_path: Path) -> None:
    """F-8：同一进程重复获取写锁视为已持有（幂等），不误抛 ProjectLockBusy。"""
    from agent.core.project_lock import acquire_project_lock

    p1 = acquire_project_lock(tmp_path, "autowrite")
    assert p1.exists()
    # 同进程二次获取（嵌套调用场景，如 CLI 持锁后再经 service.run_autowrite）
    p2 = acquire_project_lock(tmp_path, "autowrite")
    assert p2 == p1


def test_probe_lock_absent_returns_none(tmp_path: Path) -> None:
    """锁不存在时探测返回 None。"""
    from agent.core.project_lock import probe_project_lock

    assert probe_project_lock(tmp_path, "autowrite") is None


def test_probe_lock_alive_returns_holder(tmp_path: Path) -> None:
    """锁被存活进程持有时探测返回持有者信息。

    2026-09-07 L1-1 语义修正：本进程探测自己持有的锁返回 None（视为空闲）——
    否则派发层加锁后命令体内的预检会把自己误判成「他人占用」而秒退。
    「他人持有→返回持有者」改由真实子进程验证：
    """
    import subprocess
    import sys

    from agent.core.project_lock import acquire_project_lock, probe_project_lock

    # 本进程持锁 → probe 空闲（新契约）
    acquire_project_lock(tmp_path, "autowrite")
    assert probe_project_lock(tmp_path, "autowrite") is None

    # 模拟另一存活进程的视角：子进程 probe 应看到本进程（存活）为持有者
    code = (
        "import sys, json; sys.path.insert(0, 'src')\n"
        "from agent.core.project_lock import probe_project_lock\n"
        f"h = probe_project_lock(r'{tmp_path}', 'autowrite')\n"
        "print(json.dumps({'pid': (h or {}).get('pid')}))\n"
    )
    r = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60
    )
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert int(out["pid"]) == os.getpid()


# ---------------------------------------------------------------- F-7 进度
def _progress_update(total_written: int, chapter_num: int) -> int:
    """复刻 m5_persist._update_progress 的 total_written 语义（只增不回流）。"""
    return max(int(total_written or 0), int(chapter_num))


def test_progress_total_monotonic() -> None:
    """F-7：total_written 只增不回流——并发下后写进程不会用较小章节号覆盖回退。"""
    assert _progress_update(0, 1) == 1   # 第 1 章
    assert _progress_update(1, 2) == 2   # 第 2 章
    assert _progress_update(5, 5) == 5   # 等值
    # 并发竞争：A 已推进到 6，B 的 ch5 后写 → 不能回退到 5
    assert _progress_update(6, 5) == 6


def test_loop_termination_local_wrote() -> None:
    """F-7：写章循环双条件——state 滞后（并发读旧值）时本地 wrote 兜底终止。

    复刻 agentic_pipeline 的循环条件：`wrote < target - start_total and total < target`。
    场景：start_total=3、target=5（应写 2 章）；state 因并发滞后为 3（未推进），
    本地 wrote=2 已达成 → 必须停（不写第 3 章）。
    """
    start_total, target, wrote = 3, 5, 2
    total_read_from_state = 3  # 并发下 state 滞后（本该 5）
    should_continue = wrote < target - start_total and total_read_from_state < target
    assert should_continue is False, "本地 wrote 达到本轮应写数即停（防多写）"

    # 正常场景：wrote 未达 → 继续
    wrote2 = 1
    assert wrote2 < target - start_total and total_read_from_state < target
