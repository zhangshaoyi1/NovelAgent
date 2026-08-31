"""G15 P0-2 伏笔确定性状态机：thread+beats 生命周期由纯函数推导

与 M13 扁平表格并存（不替换）；import-foreshadow 做只读适配导入。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.core.story.foresight import (
    derive_status,
    mark_committed,
    ForesightBeat,
    ForesightStore,
    ForesightThread,
)
from agent.workflows.m5_write_chapter import M5WriteChapterWorkflow
from tests.conftest import make_project


def _thread(beats: list[dict]) -> ForesightThread:
    return ForesightThread(
        fid="F-01",
        core_question="镜面乱码的秘密是什么？",
        hidden_truth="宗门暗门",
        expected_resolve="ch095",
        beats=[ForesightBeat(**b) for b in beats],
    )


# ---------------- derive_status 纯函数 ----------------
def test_derive_planned_when_no_commit() -> None:
    t = _thread([{"beat_id": "b1", "type": "plant", "exec_status": "planned"}])
    assert derive_status(t) == "planned"


def test_derive_open_after_plant() -> None:
    t = _thread([
        {"beat_id": "b1", "type": "plant", "exec_status": "committed", "commit_id": "ch003"},
    ])
    assert derive_status(t) == "open"


def test_derive_progressing_after_reinforce() -> None:
    t = _thread([
        {"beat_id": "b1", "type": "plant", "exec_status": "committed", "commit_id": "ch003"},
        {"beat_id": "b2", "type": "reinforce", "exec_status": "committed", "commit_id": "ch010"},
    ])
    assert derive_status(t) == "progressing"


def test_derive_resolved_after_payoff() -> None:
    t = _thread([
        {"beat_id": "b1", "type": "plant", "exec_status": "committed", "commit_id": "ch003"},
        {"beat_id": "b2", "type": "payoff", "exec_status": "committed", "commit_id": "ch095"},
    ])
    assert derive_status(t) == "resolved"


def test_derive_status_only_committed_count() -> None:
    """只有 committed 的 beat 才推进状态；written/planned 不算。"""
    t = _thread([
        {"beat_id": "b1", "type": "plant", "exec_status": "written"},
    ])
    assert derive_status(t) == "planned"


def test_derive_writes_back_status() -> None:
    t = _thread([
        {"beat_id": "b1", "type": "plant", "exec_status": "committed", "commit_id": "ch003"},
    ])
    derive_status(t)
    assert t.status == "open"


# ---------------- committed-only-commit_id 不变式 ----------------
def test_committed_requires_commit_id() -> None:
    from agent.core.base.validation import validate_model

    ok, _, _ = validate_model(
        ForesightBeat,
        {"beat_id": "b1", "type": "plant", "exec_status": "committed"},
    )
    assert not ok


def test_mark_committed_binds_and_updates() -> None:
    t = _thread([{"beat_id": "b1", "type": "plant", "exec_status": "planned"}])
    beat = t.beats[0]
    mark_committed(t, beat, "ch003")
    assert beat.exec_status == "committed"
    assert beat.commit_id == "ch003"
    assert t.status == "open"


def test_thread_beat_unique() -> None:
    with pytest.raises(ValueError):
        _thread([
            {"beat_id": "b1", "type": "plant"},
            {"beat_id": "b1", "type": "payoff"},
        ])


# ---------------- ForesightStore 持久化 ----------------
def test_store_upsert_and_persist(tmp_path: Path) -> None:
    store = ForesightStore(tmp_path)
    assert store.load() == []
    t = _thread([
        {"beat_id": "b1", "type": "plant", "exec_status": "committed", "commit_id": "ch003"},
    ])
    store.upsert(t)
    loaded = store.load()
    assert len(loaded) == 1
    assert loaded[0].status == "open"  # save 时自动 derive
    # 再 upsert 同 fid 覆盖不重复
    t2 = _thread([
        {"beat_id": "b1", "type": "plant", "exec_status": "committed", "commit_id": "ch003"},
        {"beat_id": "b2", "type": "payoff", "exec_status": "committed", "commit_id": "ch095"},
    ])
    store.upsert(t2)
    loaded2 = store.load()
    assert len(loaded2) == 1
    assert loaded2[0].status == "resolved"


# ---------------- import-foreshadow 从 M13 扁平表格适配 ----------------
def test_import_from_m13_flat(tmp_path: Path) -> None:
    d = make_project(tmp_path, n_chapters=0)
    from agent.core.story import ForesightStore, ForesightThread
    from agent.workflows.m13_foreshadow import M13ForeshadowWorkflow

    store = ForesightStore(d)
    wf = M13ForeshadowWorkflow(project_dir=d)
    flat = wf.load_foreshadows()
    assert flat, "conftest 的 foreshadows.md 应含 F-01（未埋）"
    for f in flat:
        beats = []
        if f.state in ("已埋", "已回收"):
            beats.append({"beat_id": f"{f.fid}-plant", "type": "plant",
                          "exec_status": "committed", "commit_id": str(f.planted_at)})
        if f.state == "已回收":
            beats.append({"beat_id": f"{f.fid}-payoff", "type": "payoff",
                          "exec_status": "committed", "commit_id": str(f.planted_at)})
        thread = ForesightThread(
            fid=f.fid, core_question=f.content, hidden_truth=f.content,
            expected_resolve=f.expected_resolve, beats=beats,
        )
        store.upsert(thread)
    loaded = store.load()
    assert len(loaded) == 1
    # conftest 的 F-01 状态是「未埋」→ 无 committed beat → planned
    assert loaded[0].status == "planned"


# ---------------- M5 章后归档 hook：账本交接 + beats 落地 ----------------
def test_archive_hook_commits_handoff_and_beats(tmp_path: Path) -> None:
    d = make_project(tmp_path, n_chapters=0)
    wf = M5WriteChapterWorkflow(project_dir=d, deslop_enabled=False)

    # 预置一条伏笔线程：plant 规划锚指向 ch001
    store = ForesightStore(d)
    store.upsert(ForesightThread(
        fid="F-01", core_question="秘密？", hidden_truth="暗门",
        beats=[ForesightBeat(beat_id="F-01-plant", type="plant", anchor_chapter=1,
                             exec_status="planned")],
    ))

    # 归档第 1 章
    wf._archive_chapter({"chapter_num": 1}, "觉醒")

    from agent.core.continuity import ContinuityLedgerStore

    ledger = ContinuityLedgerStore(d)
    ledger.load()
    assert ledger.last_commit_chapter() == 1
    assert ledger.ledger.latest_handoff().summary == "第1章《觉醒》"

    threads = store.load()
    assert len(threads) == 1
    assert threads[0].beats[0].exec_status == "committed"
    assert threads[0].beats[0].commit_id == "ch001"
    assert threads[0].status == "open"  # 仅 plant 落地 → open


def test_archive_hook_degrades_on_missing_ledger_dir(tmp_path: Path) -> None:
    """缺账本/目录不存在 → 平账归档，不抛异常（降级不阻断）。"""
    d = make_project(tmp_path, n_chapters=0)
    wf = M5WriteChapterWorkflow(project_dir=d, deslop_enabled=False)
    # 不应抛任何异常
    wf._archive_chapter({"chapter_num": 1}, "觉醒")
    from agent.core.continuity import ContinuityLedgerStore

    ledger = ContinuityLedgerStore(d)
    ledger.load()
    assert ledger.last_commit_chapter() == 1