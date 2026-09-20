"""B3（H5）红线：心跳"不知道"不得升级成"杀掉"。

事故形态（process_manager.recover_running 原实现）：「心跳读不到 / 解析失败」
与「心跳确证过期」共用同一条 **杀** 分支（``fresh = False``）——
于是**心跳只是落盘失败**（磁盘/权限瞬时问题，``update_task_heartbeat`` 写不进去）
时，进程明明还活着却被"先杀后标"杀掉：**心跳落盘失败 ⇒ 误杀健康任务**。
注释还写着"解析失败回退默认/跳过"，与事实不符（实际回退到最激进的动作）。

锁死的不变式（纪律 #2/#13：动作强度 ≤ 判据可达性）：
    ① 心跳**缺失/不可解析** ⇒ 保守接管（不杀）+ 显性留痕；
    ② 心跳**确证过期** ⇒ 仍杀（真孤儿要防抢写，原语义不回归）；
    ③ pid 已死 ⇒ 标记失败（原语义不回归）；
    ④ 心跳新鲜 ⇒ 接管且**不产生**告警噪音。
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from agent.daemon import process_manager as pm
from agent.daemon import task_queue as tq


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    p = tmp_path / "book"
    (p / ".state").mkdir(parents=True)
    return p


def _make_running(project: Path, *, heartbeat: str | None, pid: int = 4242) -> dict:
    tq.submit_task(project, "write", argv=[])
    task = tq.claim_next(project, daemon_pid=pid)
    assert task is not None
    tq.update_task(project, task["task_id"], heartbeat_at=heartbeat, pid=pid)
    return task


@pytest.fixture()
def spy(monkeypatch):
    calls = {"killed": [], "degraded": []}
    monkeypatch.setattr(pm, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(
        pm, "force_terminate",
        lambda project_dir, task_id, pid, **kw: calls["killed"].append(task_id) or True,
    )
    monkeypatch.setattr(
        pm, "degrade",
        lambda where, reason, exc=None: calls["degraded"].append(where),
    )
    return calls


class TestUnknownHeartbeatIsNotKilled:
    def test_missing_heartbeat_is_adopted_not_killed(self, project: Path, spy) -> None:
        """R1：心跳字段缺失 + 进程存活 ⇒ 接管，不杀。"""
        task = _make_running(project, heartbeat=None)

        pm.recover_running(project)

        assert spy["killed"] == [], "心跳缺失却杀了健康任务"
        assert tq.get_task(project, task["task_id"])["status"] == tq.STATUS_RUNNING
        assert spy["degraded"], "未知心跳必须显性留痕（不得静默）"

    def test_unparsable_heartbeat_is_adopted_not_killed(self, project: Path, spy) -> None:
        """R2：心跳不可解析 + 进程存活 ⇒ 接管，不杀。"""
        task = _make_running(project, heartbeat="不是时间戳")

        pm.recover_running(project)

        assert spy["killed"] == [], "心跳解析失败却杀了健康任务"
        assert tq.get_task(project, task["task_id"])["status"] == tq.STATUS_RUNNING


class TestConfirmedStaleStillKills:
    def test_confirmed_stale_heartbeat_is_killed(self, project: Path, spy) -> None:
        """R3：心跳**确证过期** + 进程存活 ⇒ 仍按孤儿处理（原语义不回归）。"""
        old = (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds")
        task = _make_running(project, heartbeat=old)

        pm.recover_running(project)

        # force_terminate 被 stub ⇒ 只断言「终止被发起」，不查询被 stub 掉的后置状态
        assert task["task_id"] in spy["killed"], "确证过期的孤儿进程未被清理"

    def test_dead_pid_is_finalized(self, project: Path, monkeypatch) -> None:
        """R4：pid 已死 ⇒ 标记失败（原语义不回归）。"""
        monkeypatch.setattr(pm, "_pid_alive", lambda pid: False)
        task = _make_running(project, heartbeat=None)

        pm.recover_running(project)

        assert tq.get_task(project, task["task_id"])["status"] == tq.STATUS_FAILED

    def test_fresh_heartbeat_adopted_silently(self, project: Path, spy) -> None:
        """R5：心跳新鲜 ⇒ 接管且不产生告警噪音。"""
        fresh = datetime.now().isoformat(timespec="seconds")
        task = _make_running(project, heartbeat=fresh)

        pm.recover_running(project)

        assert spy["killed"] == []
        assert spy["degraded"] == [], f"新鲜心跳不该告警：{spy['degraded']}"
        assert tq.get_task(project, task["task_id"])["status"] == tq.STATUS_RUNNING

    def test_boundary_just_inside_window_is_fresh(self, project: Path, spy) -> None:
        """R6：刚进入窗口内（< HEARTBEAT_MAX_AGE）仍判新鲜 —— 边界不得误杀。"""
        hb = (datetime.now() - timedelta(seconds=pm.HEARTBEAT_MAX_AGE - 30)).isoformat(
            timespec="seconds"
        )
        _make_running(project, heartbeat=hb)

        pm.recover_running(project)

        assert spy["killed"] == []


def test_no_wall_clock_dependence_beyond_now(project: Path, monkeypatch) -> None:
    """R7：判据只用 `now - heartbeat` 差值，不依赖绝对时刻（防 wall-clock 脆弱）。"""
    # 一个用未来时间戳（时钟回拨/时区异常）的任务：差值 < 窗口 ⇒ 新鲜，不得杀
    monkeypatch.setattr(pm, "_pid_alive", lambda pid: True)
    future = (datetime.now() + timedelta(seconds=5)).isoformat(timespec="seconds")
    tq.submit_task(project, "write", argv=[])
    task = tq.claim_next(project, daemon_pid=1)
    tq.update_task(project, task["task_id"], heartbeat_at=future, pid=1)
    assert pm.recover_running(project) == 1
    assert tq.get_task(project, task["task_id"])["status"] == tq.STATUS_RUNNING
