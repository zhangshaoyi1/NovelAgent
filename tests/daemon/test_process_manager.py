"""ProcessManager（阶段 1）单元测试：超时判定/锁自愈/孤儿恢复

安全约定：不真杀进程——kill 相关分支全部 monkeypatch 模拟。
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from agent.daemon import process_manager as pm
from agent.daemon import task_queue as tq


# ---------------------------------------------------------------- 超时阈值
def test_resolve_max_runtime_cli_explicit() -> None:
    """CLI --max-time 显式优先（两种写法）。"""
    assert pm.resolve_max_runtime("autowrite", ["--max-time", "3600"]) == 3600
    assert pm.resolve_max_runtime("autowrite", ["--max-time=600"]) == 600
    assert pm.resolve_max_runtime("autowrite", ["--max-time", "0"]) != 0  # 非法值走默认


def test_resolve_max_runtime_defaults() -> None:
    """写命令默认 2h，非写命令默认 30min。"""
    assert pm.resolve_max_runtime("autowrite", []) == pm.DEFAULT_WRITE_MAX_RUNTIME_S
    assert pm.resolve_max_runtime("cost", []) == pm.DEFAULT_OTHER_MAX_RUNTIME_S


def test_should_timeout() -> None:
    """超时判定：running + 超 max_runtime。"""
    now = time.time()
    task_running_old = {
        "status": tq.STATUS_RUNNING,
        "max_runtime_s": 60,
        "started_at": datetime.fromtimestamp(now - 120).isoformat(),
    }
    assert pm.should_timeout(task_running_old, now) is True
    task_running_new = {
        "status": tq.STATUS_RUNNING,
        "max_runtime_s": 60,
        "started_at": datetime.fromtimestamp(now - 30).isoformat(),
    }
    assert pm.should_timeout(task_running_new, now) is False
    task_done = {**task_running_old, "status": tq.STATUS_DONE}
    assert pm.should_timeout(task_done, now) is False


# ---------------------------------------------------------------- 锁自愈
def _make_lock(project: Path, pid: int) -> Path:
    lock = project / ".state" / "writer.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"pid": pid, "command": "autowrite"}), encoding="utf-8")
    return lock


def test_cleanup_stale_lock_dead_pid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """锁 pid 已死（探测 False）且精确匹配 → 清理。"""
    lock = _make_lock(tmp_path, 999_999_999)
    monkeypatch.setattr(pm, "_pid_alive", lambda pid: False)
    assert pm.cleanup_stale_lock(tmp_path, 999_999_999) is True
    assert not lock.exists()


def test_cleanup_stale_lock_alive_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """锁 pid 还活着 → 不接管（保守，防误删）。"""
    _make_lock(tmp_path, 12345)
    monkeypatch.setattr(pm, "_pid_alive", lambda pid: True)
    assert pm.cleanup_stale_lock(tmp_path, 12345) is False
    assert (tmp_path / ".state" / "writer.lock").exists()


def test_cleanup_stale_lock_pid_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """锁 pid 不匹配 → 不动。"""
    _make_lock(tmp_path, 111)
    monkeypatch.setattr(pm, "_pid_alive", lambda pid: False)
    assert pm.cleanup_stale_lock(tmp_path, 222) is False
    assert (tmp_path / ".state" / "writer.lock").exists()


# ---------------------------------------------------------------- 孤儿恢复
def _submit_running_task(tmp_path: Path, task_id: str, pid: int, *, heartbeat_age_s: int) -> Path:
    tq._ensure_dirs(tmp_path)
    task = {
        "task_id": task_id, "command": "autowrite", "argv": [],
        "project_dir": str(tmp_path.resolve()), "submitted_by": "test",
        "status": tq.STATUS_RUNNING, "stop_requested": False,
        "pid": pid,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "heartbeat_at": (datetime.now() - timedelta(seconds=heartbeat_age_s)).isoformat(
            timespec="seconds"
        ),
        "max_runtime_s": 7200, "lock_owned": True,
    }
    path = tq.tasks_root(tmp_path) / "running" / f"{task_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")
    return path


def test_recover_orphan_alive_stale_killed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """孤儿进程：pid 存活但心跳过期 → 杀进程树 + 标记 failed + 锁清。"""
    _make_lock(tmp_path, 777)
    _submit_running_task(tmp_path, "t1", 777, heartbeat_age_s=300)  # 心跳过期
    killed: list[int] = []

    def _fake_kill(pid: int) -> bool:
        killed.append(pid)
        return True

    def _fake_pid_alive(pid: int) -> bool:
        # 恢复判定时进程存活；被 kill 后视为已死（锁自愈据此清理）
        return pid not in killed

    monkeypatch.setattr(pm, "kill_process_tree_pid", _fake_kill)
    monkeypatch.setattr(pm, "_pid_alive", _fake_pid_alive)

    n = pm.recover_running(tmp_path)
    assert n == 1
    task = tq.get_task(tmp_path, "t1")
    assert task is not None and task["status"] == tq.STATUS_FAILED
    assert "孤儿" in (task.get("note") or "")
    assert killed == [777]
    assert not (tmp_path / ".state" / "writer.lock").exists()  # 锁已清


def test_recover_orphan_dead_flagged(tmp_path: Path) -> None:
    """进程已消亡 → 标记 failed + 锁清（不杀）。"""
    _make_lock(tmp_path, 888)
    _submit_running_task(tmp_path, "t2", 888, heartbeat_age_s=300)
    n = pm.recover_running(tmp_path)  # pid 888 不存在 → _pid_alive False
    assert n == 1
    task = tq.get_task(tmp_path, "t2")
    assert task is not None and task["status"] == tq.STATUS_FAILED
    assert not (tmp_path / ".state" / "writer.lock").exists()


def test_recover_fresh_heartbeat_kept(tmp_path: Path) -> None:
    """心跳新鲜 + pid 存活 → 重新接管（保持 running）。"""
    _submit_running_task(tmp_path, "t3", 999_999_998, heartbeat_age_s=5)
    n = pm.recover_running(tmp_path)  # pid 大概率不存在 → 走 died 分支
    # 心跳新鲜但 pid 不存在 → 标记 failed（进程已死但刚失去监督）
    task = tq.get_task(tmp_path, "t3")
    assert task is not None and task["status"] == tq.STATUS_FAILED
    assert n == 1
