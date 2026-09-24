"""进展证据分级（NOT_PROGRESS 负面清单）红线测试

★ 背景病灶：``should_stall`` 原实现把"事件/进度/章节/日志任一有更新"当作健康推进，
于是心跳与巡检事件持续写 ``.events/events.jsonl``、日志持续滚动 ⇒ mtime 恒新 ⇒
停滞熔断永不触发。同族：trace 虚增 99%、心跳 unknown 误杀。

★ 借鉴 Chat On Steroids 的负面清单形态（AGENTS.md:2388-2391）：
"Page presence, reloads, metadata revisions and replayed starts do not [renew the clock]."
**只有产出算进展；痕迹一律不算。**

时间轴纪律（#24）：全部用 ``should_stall(now=...)`` 显式传钟，不拨真实时钟，
因此不存在边界抖动，也无需 ``finally`` 清理掩盖主断言。
"""

from __future__ import annotations

import os

import pytest

from agent.daemon import process_manager as pm
from agent.daemon import task_queue as tq

WINDOW = pm.PROGRESS_STALL_S  # 3600


def _task(root, command: str = "autowrite") -> dict:
    return {
        "task_id": "t-1",
        "status": tq.STATUS_RUNNING,
        "project_dir": str(root),
        "command": command,
        "argv": [command],
    }


def _touch(path, ts: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    os.utime(path, (ts, ts))


@pytest.fixture()
def proj(tmp_path):
    """项目根：默认无任何进度信号。"""
    (tmp_path / "chapters").mkdir()
    (tmp_path / ".state").mkdir()
    return tmp_path


# ------------------------------------------------------- 证据分级的三档判定
def test_evidence_real_when_output_signal_exists(proj) -> None:
    """有章节产出 ⇒ real，并返回产出型 mtime。"""
    now = 10_000.0
    _touch(proj / "chapters" / "ch001.md", now)
    level, latest = pm.progress_evidence(proj)
    assert level == pm.EVIDENCE_REAL
    assert latest == pytest.approx(now)


def test_evidence_trace_only_when_only_traces_exist(proj) -> None:
    """只有事件流/日志/状态文件 ⇒ trace_only，产出型 mtime 为 0。"""
    now = 10_000.0
    _touch(proj / ".events" / "events.jsonl", now)
    _touch(proj / ".state" / "progress.json", now)
    _touch(proj / ".state" / "tasks" / "logs" / "t-1.log", now)
    level, latest = pm.progress_evidence(proj)
    assert level == pm.EVIDENCE_TRACE_ONLY
    assert latest == 0.0


def test_evidence_none_when_nothing_exists(proj) -> None:
    """连痕迹都没有 ⇒ none（保守，交给墙钟）。"""
    assert pm.progress_evidence(proj) == (pm.EVIDENCE_NONE, 0.0)


def test_output_signal_wins_over_fresher_trace(proj) -> None:
    """★ 核心：痕迹比产出更新，也不改变"有产出"这一档——但 mtime 取产出值。"""
    now = 10_000.0
    _touch(proj / "chapters" / "ch001.md", now - 7200)  # 陈旧产出
    _touch(proj / ".events" / "events.jsonl", now)      # 全新痕迹
    level, latest = pm.progress_evidence(proj)
    assert level == pm.EVIDENCE_REAL
    assert latest == pytest.approx(now - 7200)  # 取的是产出，不是痕迹


# ------------------------------------------------- 兼容路径：默认行为不得漂移
def test_default_path_unchanged_when_only_traces_refresh(proj) -> None:
    """红线：默认（非严格）模式下，仅痕迹刷新仍判"未停滞"——向后兼容不得破。

    这条同时是"闸门强度不得超过证据"的守护（纪律 #20）：默认不硬化，
    只留痕观察。改默认前必须先有真实分布证据。
    """
    now = 10_000.0
    _touch(proj / ".events" / "events.jsonl", now)  # 痕迹恒新
    assert pm.should_stall(_task(proj), now=now) is False


def test_default_path_still_stalls_when_all_signals_old(proj) -> None:
    """默认模式下全部信号都陈旧 ⇒ 仍判停滞（原有能力不回退）。"""
    now = 10_000.0
    _touch(proj / ".events" / "events.jsonl", now - WINDOW - 10)
    assert pm.should_stall(_task(proj), now=now) is True


# --------------------------------------------------- 严格模式：只认产出型信号
def test_strict_stalls_writer_when_traces_refresh_but_output_stale(proj) -> None:
    """★ 治本用例：痕迹刷得再新、产出超窗 ⇒ 判定停滞（不再被假进展续命）。"""
    now = 10_000.0
    _touch(proj / "chapters" / "ch001.md", now - WINDOW - 10)  # 产出超窗
    _touch(proj / ".events" / "events.jsonl", now)             # 痕迹全新
    _touch(proj / ".state" / "tasks" / "logs" / "t-1.log", now)
    assert pm.should_stall(_task(proj), now=now, strict=True) is True


def test_strict_keeps_healthy_writer_running(proj) -> None:
    """邻近反向：产出在窗口内 ⇒ 不误杀健康写任务。"""
    now = 10_000.0
    _touch(proj / "chapters" / "ch001.md", now - 60)
    _touch(proj / ".events" / "events.jsonl", now)
    assert pm.should_stall(_task(proj), now=now, strict=True) is False


def test_strict_out_of_scope_for_non_writer_command(proj) -> None:
    """★ 作用域用例（纪律 #18）：非写命令不产出 chapters，严格模式不得误伤。"""
    now = 10_000.0
    _touch(proj / ".events" / "events.jsonl", now)  # 评估/统计命令只写事件流
    assert pm.should_stall(_task(proj, command="cost"), now=now, strict=True) is False
    assert pm._is_writer_task(_task(proj, command="cost")) is False


def test_strict_conservative_when_no_output_yet(proj) -> None:
    """冷启动尚无产出 ⇒ 保守不判停滞，交给墙钟（纪律：判据不可达时不硬拦）。"""
    now = 10_000.0
    _touch(proj / ".state" / "progress.json", now - WINDOW - 10)
    assert pm.should_stall(_task(proj), now=now, strict=True) is False


# --------------------------------------------------------------- 开关与留痕
def test_strict_env_switch(proj, monkeypatch) -> None:
    """严格模式默认关；环境变量 NOVEL_STRICT_PROGRESS=1 生效。"""
    now = 10_000.0
    _touch(proj / "chapters" / "ch001.md", now - WINDOW - 10)
    _touch(proj / ".events" / "events.jsonl", now)
    monkeypatch.delenv(pm.STRICT_PROGRESS_ENV, raising=False)
    assert pm._env_bool_strict() is False
    assert pm.should_stall(_task(proj), now=now) is False
    monkeypatch.setenv(pm.STRICT_PROGRESS_ENV, "1")
    assert pm._env_bool_strict() is True
    assert pm.should_stall(_task(proj), now=now) is True


def test_trace_only_leaves_visible_log(proj, caplog) -> None:
    """★ 纪律 #1：假进展必须显性化，不得静默——trace_only 时输出 warning。"""
    now = 10_000.0
    _touch(proj / ".events" / "events.jsonl", now)
    with caplog.at_level("WARNING"):
        pm.should_stall(_task(proj), now=now)
    assert any("trace-only" in r.getMessage() for r in caplog.records)


def test_signal_vocabulary_is_disjoint() -> None:
    """★ 契约红线：产出型与痕迹型信号集合必须互斥且并集覆盖原清单。

    防止后续有人把同一个路径同时放进两边（⇒ 分级语义自相矛盾），
    也防止新增信号漏掉归类（⇒ 又变成"任一更新即进展"）。
    """
    out = set(pm.PROGRESS_SIGNALS_OUTPUT)
    trace = set(pm.PROGRESS_SIGNALS_TRACE)
    assert out & trace == set(), "产出型与痕迹型信号不得重叠"
    assert out | trace == set(pm._PROGRESS_SIGNALS), "新增进度信号必须明确归类"
