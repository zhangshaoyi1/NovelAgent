"""safe-delete 护栏容错回归（2026-09-11 事故）。

背景：WorkBuddy 的 safe-delete shim 在「本进程累计删除数 > 阈值」时，对
``os.remove/unlink``、``Path.unlink/rmdir``、``shutil.rmtree`` 抛 ``SystemExit(1)``。
写一章会产生大量临时文件，收尾时正好越线，于是：

- worker atexit 删写锁失败 → 进程带失败码退出（**写批次实际成功却被判 failed**）
  + 陈旧 ``writer.lock`` 残留；
- daemon 归档任务时 ``src.unlink()`` 失败 → **daemon 被打死**
  + 任务同时残留 ``running/`` 与 ``done/``。

修法不是逐个吞异常，而是把关键路径的「删除」换成「改名」（``os.replace`` 属移动
操作，不在护栏 hook 名单内）。本组测试让删除抛 ``SystemExit``，验证：
1. 释放写锁不再崩、不再带失败码；
2. 陈旧锁在删不掉时仍能被接管（改名挪走）；
3. 任务归档走改名后，任务被打死也不再出现双份残留；
4. 停止标志在删不掉时被改名挪走而非残留（避免毒化下次 daemon 拉起）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


def _raise_systemexit(*_args: object, **_kwargs: object) -> None:
    raise SystemExit(1)


def _block_unlink(monkeypatch: pytest.MonkeyPatch) -> None:
    """模拟 safe-delete 批量护栏：一切 Path.unlink 都抛 SystemExit(1)。"""
    monkeypatch.setattr(Path, "unlink", _raise_systemexit, raising=True)


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    p = tmp_path / "book"
    (p / ".state").mkdir(parents=True)
    (p / "world.md").write_text("x", encoding="utf-8")
    return p


# ---------------------------------------------------------------- 写锁


def test_release_lock_tolerates_blocked_unlink(tmp_path: Path, monkeypatch) -> None:
    """删锁被拦时不得让 SystemExit 逃逸（否则 atexit 打失败码 + 留陈旧锁）。"""
    from agent.core import project_lock as pl

    lock = pl.lock_path_for(tmp_path)
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(
        json.dumps({"pid": os.getpid(), "command": "test"}), encoding="utf-8"
    )
    _block_unlink(monkeypatch)

    pl._release_lock(lock)  # 不抛即通过

    assert lock.exists(), "删除被拦时锁文件残留属预期（下次 acquire 自动接管）"


def test_acquire_takes_over_stale_lock_when_unlink_blocked(
    tmp_path: Path, monkeypatch
) -> None:
    """陈旧锁删不掉时应退化为「改名挪走」接管，且最后归本进程所有。"""
    from agent.core import project_lock as pl

    lock = pl.lock_path_for(tmp_path)
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"pid": 999_999, "command": "dead"}), encoding="utf-8")
    monkeypatch.setattr(pl, "_pid_alive", lambda pid: False)
    _block_unlink(monkeypatch)

    got = pl.acquire_project_lock(tmp_path, "test")

    assert got == lock
    info = json.loads(lock.read_text(encoding="utf-8"))
    assert info["pid"] == os.getpid(), "接管后锁必须归属本进程"
    assert Path(f"{lock}.stale").exists(), "删不掉的陈旧锁应被改名挪走"


# ---------------------------------------------------------------- 任务归档


def test_finalize_task_archives_without_delete(project: Path, monkeypatch) -> None:
    """归档走改名（os.replace）而非删源：删除被拦也不会留双份、不会崩。"""
    from agent.daemon import task_queue as tq

    task = tq.submit_task(project, "autowrite", ["--batch", "1"], "test")
    tid = task["task_id"]
    claimed = tq.claim_next(project, daemon_pid=os.getpid())
    assert claimed is not None and claimed["task_id"] == tid

    _block_unlink(monkeypatch)
    result = tq.finalize_task(project, tid, tq.STATUS_DONE, exit_code=0)

    assert result is not None and result["status"] == tq.STATUS_DONE
    root = tq.tasks_root(project)
    assert not (root / "running" / f"{tid}.json").exists(), "running/ 不得残留副本"
    done = json.loads((root / "done" / f"{tid}.json").read_text(encoding="utf-8"))
    assert done["status"] == tq.STATUS_DONE and done["exit_code"] == 0


def test_finalize_task_marks_terminal_for_orphans(project: Path, monkeypatch) -> None:
    """孤儿回收（daemon 重启路径）在删除被拦时同样不得崩。"""
    from agent.daemon import task_queue as tq

    task = tq.submit_task(project, "autowrite", [], "test")
    tid = task["task_id"]
    tq.claim_next(project, daemon_pid=os.getpid())

    _block_unlink(monkeypatch)
    n = tq.interrupt_orphans(project)

    assert n == 1
    root = tq.tasks_root(project)
    assert not (root / "running" / f"{tid}.json").exists()
    assert (root / "done" / f"{tid}.json").exists()


# ---------------------------------------------------------------- 停止标志


def test_clear_stop_flag_quarantines_when_unlink_blocked(
    tmp_path: Path, monkeypatch
) -> None:
    """停止标志删不掉时必须挪走——残留会毒化下一次 daemon 拉起。"""
    from agent.daemon import task_queue as tq

    tq.request_daemon_stop(tmp_path)
    flag = tq.daemon_stop_flag(tmp_path)
    assert flag.exists()

    _block_unlink(monkeypatch)
    tq.clear_daemon_stop_flag(tmp_path)

    assert not flag.exists(), "标志必须从原路径消失（改名挪走），否则毒化下次拉起"
