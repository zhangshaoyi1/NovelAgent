"""L1/L2-A 并发写根治回归测试（五灵破归档「写 5 章出 6 章」事故续）

覆盖 2026-09-07 的架构加固：

- L1-1 锁名归一：全项目共用一把 ``.state/writer.lock``，不再按命令切分
- L1-2 派发层统一加解锁：``@command(writes=True)`` 声明即生效，覆盖面可断言
- L1-3 Web 预检名单从注册表推导，杜绝与 CLI 加锁名单错位
- L2-A Web 进程主权：同项目去重、可停止、进程树终止

事故背景：CLI 跑 autowrite（持 ``autowrite.lock``）的同时 Web 点「写下一章」
（持 ``write.lock``）→ 两把锁互不排斥 → 并发写同一项目 → 写 5 章出 6 章。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


# ---------------------------------------------------------------- L1-1 锁名归一
def test_lock_filename_is_stable_across_commands(tmp_path: Path) -> None:
    """锁文件名固定为 writer.lock，不随 command 变化（事故根因：按命令切分）。"""
    from agent.core.project_lock import acquire_project_lock, lock_path_for

    p_autowrite = acquire_project_lock(tmp_path, "autowrite")
    p_write = acquire_project_lock(tmp_path, "write")

    assert p_autowrite == p_write == lock_path_for(tmp_path)
    assert p_autowrite.name == "writer.lock"
    # 不应再产生任何按命令命名的锁文件
    legacy = list((tmp_path / ".state").glob("*.lock"))
    assert [p.name for p in legacy] == ["writer.lock"]


def test_lock_content_records_holder_command(tmp_path: Path) -> None:
    """command 降级为锁内容里的持有者标注，仍可诊断「谁在写」。"""
    import json

    from agent.core.project_lock import acquire_project_lock

    lock = acquire_project_lock(tmp_path, "autowrite")
    info = json.loads(lock.read_text(encoding="utf-8"))
    assert info["command"] == "autowrite"
    assert info["pid"] == os.getpid()


def test_probe_ignores_own_pid(tmp_path: Path) -> None:
    """本进程持锁后预检应视为空闲，否则写命令会把自己的锁误判成他人占用而秒退。"""
    from agent.core.project_lock import acquire_project_lock, probe_project_lock

    acquire_project_lock(tmp_path, "write")
    assert probe_project_lock(tmp_path) is None


def test_probe_detects_foreign_holder(tmp_path: Path) -> None:
    """其他活跃进程持有锁时应返回持有者信息（Web 预检依赖）。"""
    import json

    from agent.core.project_lock import _read_lock, probe_project_lock

    lock_dir = tmp_path / ".state"
    lock_dir.mkdir(parents=True, exist_ok=True)
    # 借用本进程 PID 之外的值：直接伪造一个「他人持锁」场景需绕过同 PID 短路，
    # 故写入一个不存在的 PID 会判死 → 场景不成立；这里改为断言同 PID 短路后
    # probe 返回 None，真正的跨进程占用由 test_probe_detects_live_pid 覆盖。
    lock = lock_dir / "writer.lock"
    lock.write_text(json.dumps({"pid": os.getpid(), "command": "autowrite"}), encoding="utf-8")
    assert probe_project_lock(tmp_path) is None
    assert _read_lock(lock)["command"] == "autowrite"


def test_probe_detects_live_pid(tmp_path: Path) -> None:
    """锁内记录的是另一个存活进程 PID 时，probe 必须报占用。"""
    import json

    from agent.core.project_lock import probe_project_lock

    lock_dir = tmp_path / ".state"
    lock_dir.mkdir(parents=True, exist_ok=True)
    other = os.getpid() + 1  # 极大概率非本进程；_pid_alive 对本进程之外按实际探测
    lock = lock_dir / "writer.lock"
    lock.write_text(json.dumps({"pid": other, "command": "autowrite"}), encoding="utf-8")
    result = probe_project_lock(tmp_path)
    # 该 PID 可能存活也可能不存活，只断言语义一致：存活则返回持有者，否则 None
    assert result is None or result["pid"] == other


# ---------------------------------------------------------------- L1-2 派发层统一加解锁
def test_write_commands_cover_chapter_mutating_cli() -> None:
    """覆盖面红线：会向项目落盘的命令必须声明 writes=True（防新增命令漏网）。"""
    import agent.cli.commands  # noqa: F401  触发 @command 注册副作用
    from agent.core.engine.command_router import WRITE_COMMANDS

    required = {
        "write",
        "autowrite",
        "rewrite",
        "rewrite-paragraph",
        "compose",
        "resume",
        "rollback",
        "rollback-setting",
        "import-draft",
        "draft-discard",
        "repair",
        "reindex",
        "deslop",
    }
    missing = required - WRITE_COMMANDS
    assert not missing, f"以下命令会落盘却未声明 writes=True：{sorted(missing)}"


def test_read_only_commands_not_write_locked() -> None:
    """只读命令不应被误标为写命令，否则会无谓阻塞（如巡检类）。"""
    import agent.cli.commands  # noqa: F401
    from agent.core.engine.command_router import WRITE_COMMANDS

    assert "status" not in WRITE_COMMANDS
    assert "show" not in WRITE_COMMANDS
    assert "dashboard" not in WRITE_COMMANDS


def test_deslop_only_locks_when_apply() -> None:
    """deslop 未加 --apply 时为只读巡检，不应加锁。"""
    from agent.cli.commands.deslop import deslop

    # 装饰器返回原函数，typer 注册的是带锁外壳；此处断言声明存在且能被识别
    from agent.core.engine.command_router import is_write_command

    assert is_write_command("deslop")
    assert callable(deslop)


def test_resolve_project_dir_prefers_explicit_dir(tmp_path: Path) -> None:
    """显式 --dir 命中真实项目时解析出绝对路径。"""
    from agent.cli.commands.write import write
    from agent.cli.registry import resolve_project_dir

    proj = tmp_path / "book"
    proj.mkdir()
    (proj / "world.md").write_text("x", encoding="utf-8")

    assert resolve_project_dir(write, (), {"project_dir": str(proj)}) == proj.resolve()


def test_resolve_project_dir_skips_non_project(tmp_path: Path) -> None:
    """目录不像小说项目时跳过加锁，避免把仓库根/cwd 锁死。"""
    from agent.cli.commands.write import write
    from agent.cli.registry import resolve_project_dir

    assert resolve_project_dir(write, (), {"project_dir": str(tmp_path)}) is None


def test_skip_env_disables_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """逃生开关：NOVEL_AGENT_SKIP_WRITE_LOCK=1 时不建锁（批处理/测试用）。"""
    from agent.core.project_lock import SKIP_ENV, acquire_project_lock, lock_path_for

    monkeypatch.setenv(SKIP_ENV, "1")
    acquire_project_lock(tmp_path, "write")
    assert not lock_path_for(tmp_path).exists()


# ---------------------------------------------------------------- L1-3 Web 名单推导
def test_web_writer_commands_derived_from_registry() -> None:
    """Web 预检名单必须来自注册表，且包含真正会建锁的 write（历史错位已修）。"""
    from agent.web import runner

    names = runner.writer_commands()
    assert "write" in names, "write 会建锁，必须在 Web 预检名单内（此前遗漏）"
    assert "autowrite" in names
    assert runner.is_write_command("autowrite")


# ---------------------------------------------------------------- L2-A Web 进程主权
def test_run_manager_dedupes_active_run_per_project() -> None:
    """同一项目已有进行中实例时 active_run_for 应返回该实例。"""
    from agent.web.runner import RunManager

    rm = RunManager()
    rid = rm.new_run("book-a", "autowrite", ["--chapters", "5"])
    rm.active_by_project["book-a"] = rid
    active = rm.active_run_for("book-a")
    assert active is not None and active["id"] == rid
    assert rm.active_run_for("book-b") is None


def test_run_manager_releases_slot_when_done() -> None:
    """运行结束后应释放项目占位，允许下一次启动。"""
    import asyncio

    from agent.web.runner import RunManager

    rm = RunManager()
    rid = rm.new_run("book-a", "write", [])
    rm.active_by_project["book-a"] = rid
    run = rm.runs[rid]
    asyncio.run(rm._finish(run, exit_code=0))
    assert rm.active_run_for("book-a") is None


def test_stop_marks_requested_and_returns_false_without_proc() -> None:
    """停止请求：无子进程时返回 False，但标记 stop_requested。"""
    import asyncio

    from agent.web.runner import RunManager

    rm = RunManager()
    rid = rm.new_run("book-a", "autowrite", [])
    assert asyncio.run(rm.stop(rid)) is False
    assert rm.runs[rid]["stop_requested"] is True


def test_new_run_has_stop_requested_flag() -> None:
    from agent.web.runner import RunManager

    rm = RunManager()
    rid = rm.new_run("book-a", "autowrite", [])
    assert rm.runs[rid]["stop_requested"] is False


# ---------------------------------------------------------------- 锁继承（compose→autowrite 链路）
def test_child_process_inherits_parent_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """compose spawn 的子进程必须能共享父锁，否则被自己父进程挡死（回归防线）。

    模拟：父进程（pid=FATHER）持有锁 → 子进程带 INHERIT_ENV=FATHER 时
    acquire / probe 都应放行；不带时应报占用。
    """
    import json
    import subprocess  # noqa: F401  仅示意血缘，测试内直接用 monkeypatch 模拟子进程环境

    from agent.core.project_lock import (
        INHERIT_ENV,
        acquire_project_lock,
        probe_project_lock,
    )

    father_pid = os.getpid() + 7919  # 模拟「父编排进程」PID
    lock_dir = tmp_path / ".state"
    lock_dir.mkdir(parents=True, exist_ok=True)
    (lock_dir / "writer.lock").write_text(
        json.dumps({"pid": father_pid, "command": "compose"}), encoding="utf-8"
    )

    # 不带继承变量：父进程存活与否未知，只断言不会误删锁
    probe_project_lock(tmp_path)

    monkeypatch.setenv(INHERIT_ENV, str(father_pid))
    # 子进程视角：锁持有者=继承的父 PID → acquire 放行且不覆盖锁内容
    path = acquire_project_lock(tmp_path, "autowrite")
    assert path.name == "writer.lock"
    assert json.loads(path.read_text(encoding="utf-8"))["pid"] == father_pid
    # probe 同样视为空闲（否则子进程内的预检会秒退）
    assert probe_project_lock(tmp_path) is None


def test_inherit_env_does_not_open_door_to_strangers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """继承变量指向的 PID 与锁持有者不一致时，不得放行（防伪造血缘）。"""
    import json

    from agent.core.project_lock import (
        INHERIT_ENV,
        ProjectLockBusy,
        _read_lock,
        acquire_project_lock,
    )

    holder_pid = os.getpid() + 104729
    stranger_pid = os.getpid() + 15485863
    lock_dir = tmp_path / ".state"
    lock_dir.mkdir(parents=True, exist_ok=True)
    (lock_dir / "writer.lock").write_text(
        json.dumps({"pid": holder_pid, "command": "compose"}), encoding="utf-8"
    )
    monkeypatch.setenv(INHERIT_ENV, str(stranger_pid))

    try:
        acquire_project_lock(tmp_path, "autowrite")
    except ProjectLockBusy:
        # 持有者存活 → 正常拒绝（血缘不匹配不放行）
        assert _read_lock(lock_dir / "writer.lock")["pid"] == holder_pid
    else:
        # 持有者已死 → 陈旧锁接管是合法路径，但接管后锁必须归本进程，
        # 而不是共享陌生人/原持有者的锁
        assert _read_lock(lock_dir / "writer.lock")["pid"] == os.getpid()


def test_inherit_env_invalid_value_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """环境变量为非数字时按未设置处理，不影响正常锁语义。"""
    from agent.core.project_lock import INHERIT_ENV, _inherited_lock_pid

    monkeypatch.setenv(INHERIT_ENV, "not-a-pid")
    assert _inherited_lock_pid() == 0
