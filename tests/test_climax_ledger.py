"""高潮账本红线（登记单 20261001_信任链与叙事上限六项能力·子项 6，PRD B7）。

守卫验收标准：
- **登记**：从 MasterPlan 弧线确定性推导（每弧一主峰，蓄势来源=预计回收点落在
  弧区间内的伏笔），账本唯一读写口、原子落盘、空账自举幂等。
- **写前注入**：蓄势章/引爆章各有指令文本，引爆章给登记判据；skipped/detonated
  不再注入。
- **状态推进**：charging 后到峰——蓄势伏笔在 G15 账本本章 resolve → detonated；
  逾期（峰+宽限）仍未兑现 → skipped 留因。
- **告警**：跳过点名 / 逾期未引爆 / 爆点间隔超阈（确定性）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.core.story.climax_ledger import (
    CLIMAX_LEDGER_FILE,
    SKIP_GRACE_CHAPTERS,
    ClimaxEvent,
    ClimaxLedgerStore,
    climax_context_text,
    derive_from_plan,
    ensure_ledger,
    ledger_warnings,
    update_status_after_chapter,
)


def _write_plan(tmp_path: Path, arcs=None, foreshadows=None) -> None:
    (tmp_path / ".state").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".state" / "plan.json").write_text(
        json.dumps({
            "episode_tree": arcs
            or [
                {"id": "A1", "name": "立势", "chapter_start": 1, "chapter_end": 10, "goal": "立足宗门"},
                {"id": "A2", "name": "夺旗", "chapter_start": 11, "chapter_end": 22, "goal": "夺得令旗"},
            ],
            "foreshadow_plan": foreshadows
            or [
                {"id": "F1", "content": "神秘玉佩", "plant_at_est": 3, "expected_resolve_est": 10},
                {"id": "F2", "content": "师兄背叛", "plant_at_est": 12, "expected_resolve_est": 22},
            ],
        }),
        encoding="utf-8",
    )


# ---------------------------------------------------------------- 提取与自举
def test_derive_one_main_per_arc_with_feeders(tmp_path) -> None:
    _write_plan(tmp_path)
    events = derive_from_plan(tmp_path)
    assert len(events) == 2
    e1, e2 = events
    assert e1.peak_chapter == 10 and e1.climax_type == "main"
    assert e1.feeders == ["F1"], "预计回收点落在弧区间内的伏笔应成为蓄势来源"
    assert e2.feeders == ["F2"]
    assert "兑现伏笔 F2" in e2.criteria


def test_ensure_ledger_bootstraps_idempotently(tmp_path) -> None:
    _write_plan(tmp_path)
    first = ensure_ledger(tmp_path)
    assert len(first) == 2
    # 幂等：非空账不重建（手工改动不被覆盖）
    store = ClimaxLedgerStore(tmp_path)
    events = store.load()
    events[0].title = "手工标注"
    store.save(events)
    again = ensure_ledger(tmp_path)
    assert again[0].title == "手工标注"


def test_derive_without_plan_returns_empty(tmp_path) -> None:
    assert derive_from_plan(tmp_path) == []
    assert ensure_ledger(tmp_path) == []


# ---------------------------------------------------------------- 状态推进
def _seed_event(tmp_path: Path, **kw) -> ClimaxEvent:
    e = ClimaxEvent(
        id="climax-A1", title="《立势》卷末主峰",
        chapter_start=6, peak_chapter=10, chapter_end=10,
        feeders=["F1"], criteria="兑现伏笔 F1",
        **kw,
    )
    ClimaxLedgerStore(tmp_path).upsert(e)
    return e


def test_status_progression_charging_then_detonated(tmp_path) -> None:
    _seed_event(tmp_path)
    update_status_after_chapter(tmp_path, 6)
    assert ClimaxLedgerStore(tmp_path).load()[0].status == "charging"
    # G15 账本：F1 于第 10 章 resolve
    (tmp_path / ".state" / "continuity_ledger.json").parent.mkdir(parents=True, exist_ok=True)
    # 账本文件名以 ContinuityLedgerStore 实现为准——这里 monkeypatch 查询函数更稳
    import agent.core.story.climax_ledger as cl

    orig = cl._resolved_loops_this_chapter
    cl._resolved_loops_this_chapter = lambda pd, ch: {"F1"} if ch == 10 else set()
    try:
        update_status_after_chapter(tmp_path, 9)
        assert ClimaxLedgerStore(tmp_path).load()[0].status == "charging"
        update_status_after_chapter(tmp_path, 10)
        e = ClimaxLedgerStore(tmp_path).load()[0]
        assert e.status == "detonated" and e.detonated_ch == 10
    finally:
        cl._resolved_loops_this_chapter = orig


def test_overdue_event_skipped_with_reason(tmp_path) -> None:
    _seed_event(tmp_path)
    import agent.core.story.climax_ledger as cl

    orig = cl._resolved_loops_this_chapter
    cl._resolved_loops_this_chapter = lambda pd, ch: set()
    try:
        update_status_after_chapter(tmp_path, 10 + SKIP_GRACE_CHAPTERS + 1)
    finally:
        cl._resolved_loops_this_chapter = orig
    e = ClimaxLedgerStore(tmp_path).load()[0]
    assert e.status == "skipped" and "仍未兑现" in e.skip_reason


def test_no_double_detonation(tmp_path) -> None:
    _seed_event(tmp_path, status="detonated", detonated_ch=10)
    import agent.core.story.climax_ledger as cl

    orig = cl._resolved_loops_this_chapter
    cl._resolved_loops_this_chapter = lambda pd, ch: {"F1"}
    try:
        update_status_after_chapter(tmp_path, 11)
    finally:
        cl._resolved_loops_this_chapter = orig
    e = ClimaxLedgerStore(tmp_path).load()[0]
    assert e.detonated_ch == 10, "已引爆事件不得被后续章改写"


# ---------------------------------------------------------------- 写前注入
def test_context_text_charging_vs_peak(tmp_path) -> None:
    _seed_event(tmp_path)
    update_status_after_chapter(tmp_path, 6)
    charging = climax_context_text(tmp_path, 7)
    assert "蓄势段" in charging and "第 10 章引爆" in charging
    peak = climax_context_text(tmp_path, 10)
    assert "高潮引爆章" in peak and "兑现伏笔 F1" in peak


def test_context_empty_for_irrelevant_chapter(tmp_path) -> None:
    _seed_event(tmp_path)
    assert climax_context_text(tmp_path, 30) == ""


# ---------------------------------------------------------------- 告警
def test_warnings_skip_overdue_and_gap(tmp_path) -> None:
    store = ClimaxLedgerStore(tmp_path)
    store.save([
        ClimaxEvent(id="c1", title="《甲》主峰", chapter_start=5, peak_chapter=10,
                    chapter_end=10, feeders=["F1"], status="skipped",
                    skip_reason="至第 13 章蓄势来源仍未兑现"),
        ClimaxEvent(id="c2", title="《乙》主峰", chapter_start=30, peak_chapter=40,
                    chapter_end=40, feeders=[], status="pending"),
    ])
    warnings = ledger_warnings(tmp_path, current_chapter=45)
    assert any("被跳过" in w for w in warnings)
    assert any("逾期未引爆" in w for w in warnings)
    # c1 引爆记录不存在 → 无间隔告警（last_detonated=0 不判间隔）
    store.save([
        ClimaxEvent(id="c3", title="《丙》主峰", chapter_start=5, peak_chapter=10,
                    chapter_end=10, feeders=[], status="detonated", detonated_ch=10),
    ])
    warnings = ledger_warnings(tmp_path, current_chapter=45)
    assert any("爆点间隔超限" in w for w in warnings)
    assert not any("逾期" in w for w in warnings)


def test_store_roundtrip_and_corrupt_file(tmp_path) -> None:
    store = ClimaxLedgerStore(tmp_path)
    store.upsert(ClimaxEvent(id="c1", title="t", chapter_start=1, peak_chapter=5, chapter_end=5))
    assert len(store.load()) == 1
    # 坏文件 → 空账 + 不抛
    (tmp_path / CLIMAX_LEDGER_FILE).write_text("{broken", encoding="utf-8")
    assert store.load() == []
