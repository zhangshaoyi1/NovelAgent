"""D3 路由 + unlock 回归测试。

覆盖：
- 路由开关注入：写命令 help 暴露 --direct / --wait/--no-wait；
- _strip_argv：带值选项与布尔开关剥离；
- --json / PYTEST / 进程内直调 → 不路由（直跑语义保持）；
- 路由全链路（进程内模拟 daemon）：提交 → 伪 daemon 认领归档 → 跟随返回退出码；
- follow_task：done→0 / stopped→130 / failed 透传退出码 / Ctrl+C→停止标记；
- unlock：无锁 / 陈旧锁删除 / 存活拒绝 / --force 覆盖。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

runner = CliRunner()


def _make_project(tmp_path: Path) -> Path:
    p = tmp_path / "book"
    (p / ".state").mkdir(parents=True)
    (p / "world.md").write_text("x", encoding="utf-8")
    return p


# ---------------------------------------------------------------- 路由开关
def test_write_command_exposes_route_flags() -> None:
    """写命令 help 必须暴露 D3 路由开关（合成 __signature__ 生效）。"""
    import agent.cli.commands  # noqa: F401  # 触发注册
    from agent.cli._app import app

    result = runner.invoke(app, ["write", "--help"])
    assert result.exit_code == 0
    assert "--direct" in result.output
    assert "--no-wait" in result.output


def test_strip_argv_value_and_bool_flags() -> None:
    from agent.cli.registry import _strip_argv

    argv = ["--dir", "proj", "--mode", "heavy", "--direct", "--wait", "-d", "other"]
    out = _strip_argv(argv, {"--dir", "-d"}, ("--direct", "--wait", "--no-wait"))
    assert out == ["--mode", "heavy"]
    # = 形式
    out2 = _strip_argv(["--dir=proj", "--chapters", "5"], {"--dir"}, ())
    assert out2 == ["--chapters", "5"]


def test_capture_raw_argv_none_in_process() -> None:
    """进程内直调（sys.argv 无命令名）→ None → 不路由。"""
    from agent.cli.registry import _capture_raw_argv

    assert _capture_raw_argv("reindex") is None  # pytest 的 argv 无此命令


# ---------------------------------------------------------------- 直跑豁免
def test_json_invocation_stays_direct(tmp_path: Path) -> None:
    """--json 保持直跑：不产生任务队列（stdout 信封契约不变）。"""
    import agent.cli.commands  # noqa: F401
    from agent.cli._app import app

    proj = _make_project(tmp_path)
    result = runner.invoke(app, ["reindex", "--json", "-d", str(proj)])
    assert result.exit_code == 0, result.output
    assert not (proj / ".state" / "tasks").exists(), "--json 不得路由进队列"


def test_pytest_env_stays_direct(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PYTEST 环境直跑（显式断言守卫，防止未来误删该逃生口）。"""
    import agent.cli.commands  # noqa: F401
    from agent.cli._app import app

    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests::test_pytest_env_stays_direct")
    proj = _make_project(tmp_path)
    result = runner.invoke(app, ["reindex", "-d", str(proj)])
    assert result.exit_code == 0, result.output
    assert not (proj / ".state" / "tasks").exists()


# ---------------------------------------------------------------- 路由全链路
def test_route_via_queue_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """默认调用：提交队列 → 伪 daemon 认领执行归档 → 跟随回放 → exit 0。"""
    import agent.cli.commands  # noqa: F401
    from agent.cli import registry
    from agent.cli._app import app
    from agent.daemon import task_queue as tq

    proj = _make_project(tmp_path)

    # 绕过测试逃生口，模拟真实 CLI 进程
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(
        sys, "argv", ["agent.cli", "reindex", "--dir", str(proj)]
    )
    monkeypatch.setattr("agent.daemon.core.ensure_daemon", lambda roots: True)

    # 伪 daemon：后台认领 → 直接归档 done
    def fake_daemon() -> None:
        deadline = time.time() + 10
        while time.time() < deadline:
            task = tq.claim_next(proj, daemon_pid=os.getpid())
            if task is None:
                time.sleep(0.05)
                continue
            tq.finalize_task(proj, task["task_id"], tq.STATUS_DONE, exit_code=0)
            return

    t = threading.Thread(target=fake_daemon, daemon=True)
    t.start()

    result = runner.invoke(app, ["reindex", "--dir", str(proj)])
    t.join(timeout=5)
    assert result.exit_code == 0, result.output
    assert "任务已提交" in result.output
    done = list(tq.iter_tasks(proj, tq.STATUS_DONE))
    assert len(done) == 1 and done[0]["command"] == "reindex"


def test_follow_task_exit_codes(tmp_path: Path) -> None:
    """follow_task 退出码映射：done→0 / stopped→130 / failed 透传。"""
    from agent.daemon import task_queue as tq
    from agent.daemon.follow import follow_task

    proj = _make_project(tmp_path)

    tq.submit_task(proj, "write", argv=[])
    tasks = list(tq.iter_tasks(proj, tq.STATUS_QUEUED))
    tid = tasks[0]["task_id"]
    tq.claim_next(proj, daemon_pid=1)
    tq.finalize_task(proj, tid, tq.STATUS_DONE, exit_code=0)
    assert follow_task(proj, tid, poll_interval=0.01, echo=False) == 0

    tq.submit_task(proj, "write", argv=[])
    tid2 = list(tq.iter_tasks(proj, tq.STATUS_QUEUED))[0]["task_id"]
    tq.claim_next(proj, daemon_pid=1)
    tq.finalize_task(proj, tid2, tq.STATUS_STOPPED)
    assert follow_task(proj, tid2, poll_interval=0.01, echo=False) == 130

    tq.submit_task(proj, "write", argv=[])
    tid3 = list(tq.iter_tasks(proj, tq.STATUS_QUEUED))[0]["task_id"]
    tq.claim_next(proj, daemon_pid=1)
    tq.finalize_task(proj, tid3, tq.STATUS_FAILED, exit_code=2)
    assert follow_task(proj, tid3, poll_interval=0.01, echo=False) == 2


def test_follow_ctrl_c_requests_stop(tmp_path: Path) -> None:
    """Ctrl+C：写停止标记并继续跟随；再次 Ctrl+C 强制退出 130。"""
    from agent.daemon import task_queue as tq
    from agent.daemon import follow as fl

    proj = _make_project(tmp_path)
    tq.submit_task(proj, "write", argv=[])
    tid = list(tq.iter_tasks(proj, tq.STATUS_QUEUED))[0]["task_id"]

    calls = {"n": 0}
    real_sleep = time.sleep

    def fake_sleep(_: float) -> None:
        calls["n"] += 1
        real_sleep(0.01)
        raise KeyboardInterrupt

    monkey_sleep = fl.time.sleep
    fl.time.sleep = fake_sleep  # type: ignore[assignment]
    try:
        rc = fl.follow_task(proj, tid, echo=False)
    finally:
        fl.time.sleep = monkey_sleep  # type: ignore[assignment]

    # 第一次 Ctrl+C → 停止标记（排队任务直接取消为 stopped）→ 跟随到终态返回 130
    assert rc == 130
    task = tq.get_task(proj, tid)
    assert task is not None and task["status"] == tq.STATUS_STOPPED


# ---------------------------------------------------------------- unlock
def _write_lock(proj: Path, pid: int) -> Path:
    lock = proj / ".state" / "writer.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(
        json.dumps({"pid": pid, "command": "autowrite", "started_at": "t"}),
        encoding="utf-8",
    )
    return lock


def test_unlock_no_lock(tmp_path: Path) -> None:
    import agent.cli.commands  # noqa: F401
    from agent.cli._app import app

    proj = _make_project(tmp_path)
    result = runner.invoke(app, ["unlock", "-d", str(proj)])
    assert result.exit_code == 0
    assert "无写锁" in result.output


def test_unlock_stale_lock_removed(tmp_path: Path) -> None:
    import agent.cli.commands  # noqa: F401
    from agent.cli._app import app

    proj = _make_project(tmp_path)
    _write_lock(proj, pid=os.getpid() + 99991)  # 不存在的 pid → 判死
    result = runner.invoke(app, ["unlock", "-d", str(proj)])
    assert result.exit_code == 0, result.output
    assert not (proj / ".state" / "writer.lock").exists()


def test_unlock_alive_holder_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """持有者存活 → 拒绝（exit 2），锁保留——防止重新打开并发写缺口。"""
    import agent.cli.commands  # noqa: F401
    from agent.cli._app import app
    from agent.core import project_lock

    proj = _make_project(tmp_path)
    _write_lock(proj, pid=424242)
    monkeypatch.setattr(project_lock, "_pid_alive", lambda pid: pid == 424242)

    result = runner.invoke(app, ["unlock", "-d", str(proj)])
    assert result.exit_code == 2
    assert (proj / ".state" / "writer.lock").exists(), "存活持有者的锁不得被删除"

    # --force 显式覆盖 → 删除
    result2 = runner.invoke(app, ["unlock", "-d", str(proj), "--force"])
    assert result2.exit_code == 0, result2.output
    assert not (proj / ".state" / "writer.lock").exists()
