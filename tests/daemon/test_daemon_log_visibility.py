"""A3 红线：daemon 自身的 stdout/stderr 必须落盘（降级在运行时可见）。

起因（真实缺口）：``ensure_daemon`` 用 ``stdout=DEVNULL, stderr=DEVNULL`` 拉起
detached daemon ⇒ ``degrade()`` 的 WARNING（无 handler 时经 ``logging.lastResort``
落 stderr）与一切 ``print`` **全部丢弃**，降级/熔断/落盘失败在运行时完全不可见；
而 CLI 失败提示却指向「``<root>/.daemon/`` 日志」，那里原本空无一物。

锁死的不变式：
    ① 落点 = ``<root>/.daemon/daemon.log``（与 heartbeat.json / stop.flag 同目录）；
    ② 拉起参数：``stdout`` 是指向该文件的句柄（**不是 DEVNULL**）、``stderr=STDOUT``；
    ③ daemon 禁缓冲（``-u`` / PYTHONUNBUFFERED）——否则崩溃前最后几行留不下；
    ④ **行为级**：子进程调用 ``degrade()`` 后，告警真出现在被重定向的日志里；
    ⑤ 落盘不可用时不阻断拉起（退回 DEVNULL，不抛异常）。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import agent as _agent_pkg

from agent.daemon import core as dcore
from agent.daemon import task_queue as tq

SRC_DIR = Path(_agent_pkg.__file__).resolve().parents[1]
REPO_ROOT = SRC_DIR.parent


def test_log_path_under_daemon_dir(tmp_path: Path) -> None:
    """R1：落点与心跳/停止标志同目录。"""
    assert tq.daemon_log_path(tmp_path) == tmp_path / ".daemon" / "daemon.log"


def _patch_spawn(monkeypatch, capture: dict) -> None:
    def _fake_popen(args, **kwargs):
        capture["args"] = list(args)
        capture["kwargs"] = kwargs
        fh = kwargs.get("stdout")
        capture["fh_name"] = getattr(fh, "name", None)
        return None

    monkeypatch.setattr(dcore.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(dcore.tq, "daemon_alive", lambda r: False)
    monkeypatch.setattr(dcore.tq, "heartbeat_alive", lambda r, max_age=20: False)
    monkeypatch.setattr(dcore.time, "sleep", lambda *_: None)


def test_ensure_daemon_redirects_output_to_log(tmp_path: Path, monkeypatch) -> None:
    """R2/R3：stdout 必须是指向 daemon.log 的句柄，stderr 串流，且禁缓冲。"""
    cap: dict = {}
    _patch_spawn(monkeypatch, cap)
    root = tmp_path / "novels"
    root.mkdir()

    assert dcore.ensure_daemon([root]) is False  # 心跳不推进 ⇒ False（沿用既有语义）

    assert cap["kwargs"]["stdout"] is not subprocess.DEVNULL, "daemon 输出被丢进 DEVNULL"
    assert cap["kwargs"]["stderr"] is subprocess.STDOUT, "stderr 必须与 stdout 同流"
    assert cap["fh_name"] and str(cap["fh_name"]).endswith(
        os.path.join(".daemon", "daemon.log")
    ), f"日志句柄指向了 {cap['fh_name']}"
    assert "-u" in cap["args"] or cap["kwargs"]["env"].get("PYTHONUNBUFFERED") == "1", (
        "daemon 未禁缓冲：崩溃前最后几行（含 degrade 告警）会丢失"
    )
    assert (root / ".daemon").is_dir()


def test_degrade_warning_reaches_redirected_log(tmp_path: Path) -> None:
    """R4（行为级，纪律 #10）：degrade() 的告警必须真的落到被重定向的文件里。"""
    log = tmp_path / "daemon.log"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    code = (
        "from agent.core.infra.degrade import degrade;"
        "degrade('a3.probe', 'probe reason')"
    )
    with log.open("ab", buffering=0) as fh:
        subprocess.run(
            [sys.executable, "-u", "-c", code],
            cwd=str(REPO_ROOT), env=env,
            stdout=fh, stderr=subprocess.STDOUT,
            timeout=180, check=False,
        )
    text = log.read_text(encoding="utf-8", errors="replace")
    assert "[degrade]" in text, f"降级告警没进日志：{text[:300]!r}"
    assert "a3.probe" in text


def test_ensure_daemon_survives_unusable_log_dir(tmp_path: Path, monkeypatch) -> None:
    """R5：日志目录不可用时不得抛异常，退回 DEVNULL 且照常尝试拉起。"""
    cap: dict = {}
    _patch_spawn(monkeypatch, cap)
    root = tmp_path / "novels"
    root.mkdir()
    (root / ".daemon").write_text("占位为文件 ⇒ mkdir 必失败", encoding="utf-8")

    assert dcore.ensure_daemon([root]) is False  # 不抛异常
    assert cap["kwargs"]["stdout"] is subprocess.DEVNULL, "落盘不可用时应退回 DEVNULL"
