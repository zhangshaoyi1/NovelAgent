"""D1+D2 回归测试：落盘任务队列 + writer daemon 单一权威。

覆盖 Phase 6 daemon 化的核心语义：

- TaskStore：原子提交、认领、终态归档、停止语义（排队取消 / 运行置标记）
- 崩溃恢复：遗留 running 任务在 daemon 启动时标记 failed
- daemon 消费：串行执行真实子进程任务、日志捕获、终态判定
- Web 薄客户端：RunManager 提交任务而非 spawn、stop 走任务标记
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    p = tmp_path / "book"
    (p / ".state").mkdir(parents=True)
    (p / "world.md").write_text("x", encoding="utf-8")
    return p


# ---------------------------------------------------------------- TaskStore
def test_submit_creates_pending_task_atomic(project: Path) -> None:
    from agent.daemon import task_queue as tq

    task = tq.submit_task(project, "autowrite", argv=["--chapters", "5"], submitted_by="web")
    assert task["status"] == tq.STATUS_QUEUED
    pending = list((tq.tasks_root(project) / "pending").glob("*.json"))
    assert len(pending) == 1
    assert not list(tq.tasks_root(project).glob("*.tmp"))  # 无半截写入残留
    loaded = json.loads(pending[0].read_text(encoding="utf-8"))
    assert loaded["command"] == "autowrite"
    assert loaded["argv"] == ["--chapters", "5"]


def test_claim_moves_pending_to_running_once(project: Path) -> None:
    from agent.daemon import task_queue as tq

    tq.submit_task(project, "write", argv=[])
    first = tq.claim_next(project, daemon_pid=123)
    assert first is not None and first["status"] == tq.STATUS_RUNNING
    # 已无排队任务：第二次认领必须返回 None（原子 rename 不会重复认领）
    assert tq.claim_next(project, daemon_pid=123) is None
    running = list((tq.tasks_root(project) / "running").glob("*.json"))
    assert len(running) == 1


def test_request_stop_cancels_queued_task(project: Path) -> None:
    from agent.daemon import task_queue as tq

    task = tq.submit_task(project, "write", argv=[])
    result = tq.request_stop(project, task["task_id"])
    assert result == "cancelled"
    done = tq.get_task(project, task["task_id"])
    assert done is not None and done["status"] == tq.STATUS_STOPPED
    assert not list((tq.tasks_root(project) / "pending").glob("*.json"))


def test_request_stop_signals_running_task(project: Path) -> None:
    from agent.daemon import task_queue as tq

    task = tq.submit_task(project, "write", argv=[])
    tq.claim_next(project, daemon_pid=1)
    assert tq.request_stop(project, task["task_id"]) == "signaled"
    running = tq.get_task(project, task["task_id"])
    assert running is not None and running["stop_requested"] is True


def test_finalize_archives_with_status(project: Path) -> None:
    from agent.daemon import task_queue as tq

    task = tq.submit_task(project, "write", argv=[])
    tq.claim_next(project, daemon_pid=1)
    tq.finalize_task(project, task["task_id"], tq.STATUS_FAILED, exit_code=3, note="boom")
    done = tq.get_task(project, task["task_id"])
    assert done["status"] == tq.STATUS_FAILED
    assert done["exit_code"] == 3
    assert done["note"] == "boom"
    assert not (tq.tasks_root(project) / "running" / f"{task['task_id']}.json").exists()


def test_interrupt_orphans_marks_running_failed(project: Path) -> None:
    """daemon 崩溃恢复：遗留 running → failed，不自动重跑（任务不幂等）。"""
    from agent.daemon import task_queue as tq

    task = tq.submit_task(project, "autowrite", argv=[])
    tq.claim_next(project, daemon_pid=1)
    n = tq.interrupt_orphans(project)
    assert n == 1
    done = tq.get_task(project, task["task_id"])
    assert done["status"] == tq.STATUS_FAILED
    assert "中断" in (done.get("note") or "")


def test_heartbeat_lifecycle(project: Path) -> None:
    from agent.daemon import task_queue as tq

    assert tq.heartbeat_alive(project) is False
    tq.write_heartbeat(project, pid=4242)
    assert tq.heartbeat_alive(project) is True
    # 陈旧心跳（超过阈值）不算存活
    data = tq.read_heartbeat(project) or {}
    data["ts"] = time.time() - tq.HEARTBEAT_MAX_AGE - 5
    (tq.heartbeat_path(project)).write_text(json.dumps(data), encoding="utf-8")
    assert tq.heartbeat_alive(project) is False


# ---------------------------------------------------------------- daemon 执行
def test_daemon_executes_task_serially(project: Path) -> None:
    """真实子进程执行：认领 → 运行 → 日志落盘 → done 归档（冒烟级）。"""
    from agent.daemon import task_queue as tq
    from agent.daemon.core import WriterDaemon

    tq.submit_task(project, "status", argv=[])
    d = WriterDaemon([project.parent], poll_interval=0.1)
    d._recover_orphans()
    deadline = time.time() + 60
    while time.time() < deadline:
        if d._child is None:
            d._try_claim_and_spawn()
        else:
            d._poll_child()
        done = [t for t in tq.iter_tasks(project, tq.STATUS_DONE)]
        if done:
            break
        time.sleep(0.2)
    assert done, "60s 内未完成任务"
    assert done[0]["exit_code"] == 0
    log_path = tq.tasks_root(project) / "logs" / f"{done[0]['task_id']}.log"
    assert log_path.exists() and log_path.stat().st_size > 0


def test_daemon_rejects_second_task_while_running(project: Path) -> None:
    """全局串行：有子进程在跑时不再认领新任务。"""
    from agent.daemon import task_queue as tq
    from agent.daemon.core import WriterDaemon

    tq.submit_task(project, "status", argv=[])
    d = WriterDaemon([project.parent], poll_interval=0.1)
    d._recover_orphans()
    d._try_claim_and_spawn()
    assert d._child is not None
    tq.submit_task(project, "status", argv=[])
    d._try_claim_and_spawn()  # 有子进程时该方法根本不会进入认领分支
    assert d._child is not None
    assert len(list((tq.tasks_root(project) / "running").glob("*.json"))) == 1
    assert len(list((tq.tasks_root(project) / "pending").glob("*.json"))) == 1
    # 清理：杀掉子进程避免测试残留
    from agent.daemon.core import kill_process_tree_pid

    kill_process_tree_pid(d._child.pid)


# ---------------------------------------------------------------- Web 薄客户端
def test_run_manager_stop_writes_task_flag(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Web stop → 任务文件 stop_requested（daemon 据此杀进程树）。"""
    import asyncio

    from agent.daemon import task_queue as tq
    from agent.web import runner as web_runner

    # 模拟 daemon 已认领的运行中任务
    task = tq.submit_task(project, "autowrite", argv=[])
    tq.claim_next(project, daemon_pid=1)

    monkeypatch.setattr(web_runner, "project_path", lambda name: project)
    rm = web_runner.RunManager()
    rid = rm.new_run("book", "autowrite", [])
    rm.runs[rid]["task_id"] = task["task_id"]

    stopped = asyncio.run(rm.stop(rid))
    assert stopped is True
    current = tq.get_task(project, task["task_id"])
    assert current is not None and current["stop_requested"] is True
    assert rm.runs[rid]["stop_requested"] is True


def test_run_manager_stop_returns_false_for_unknown_task(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent.daemon import task_queue as tq  # noqa: F401
    from agent.web import runner as web_runner

    monkeypatch.setattr(web_runner, "project_path", lambda name: project)
    rm = web_runner.RunManager()
    rid = rm.new_run("book", "autowrite", [])
    rm.runs[rid]["task_id"] = "20990101000000-deadbeef"  # 队列中不存在
    import asyncio

    assert asyncio.run(rm.stop(rid)) is False


def test_web_writer_commands_unchanged_by_daemon() -> None:
    """daemon 化不改变 Web 预检名单来源（注册表单一真相源仍生效）。"""
    from agent.web import runner

    names = runner.writer_commands()
    assert "autowrite" in names and "write" in names
