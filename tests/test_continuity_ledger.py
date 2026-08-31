"""G15 P0-1 连续性账本：commit 归档、有界投影去重、损坏降级"""

from __future__ import annotations

from pathlib import Path

from agent.core.continuity.derive import (
    derive_open_loop_status,
    expected_resolve_chapter,
    is_overdue,
)
from agent.core.continuity.ledger import ContinuityLedgerStore
from agent.core.continuity.models import (
    ContinuityFact,
    ContinuityHandoff,
    ContinuityKnowledge,
    ContinuityOpenLoop,
)
from agent.core.continuity.projection import project, project_to_text


def test_commit_fact_overwrite_by_key(tmp_path: Path) -> None:
    s = ContinuityLedgerStore(tmp_path)
    s.load()
    s.commit(
        chapter=1,
        facts=[
            ContinuityFact(domain="character", subject_id="林寻", field="alive",
                           value="true", source_commit_id="ch001", evidence="e"),
        ],
        knowledge=[], open_loops=[], handoff=_handoff(1),
    )
    # 同 key 覆盖更新 value
    s.commit(
        chapter=2,
        facts=[
            ContinuityFact(domain="character", subject_id="林寻", field="alive",
                           value="false", source_commit_id="ch002", evidence="e2"),
        ],
        knowledge=[], open_loops=[], handoff=_handoff(2),
    )
    assert len(s.ledger.facts) == 1  # 覆盖而非堆叠
    assert s.ledger.facts[0].value == "false"
    assert s.ledger.facts[0].source_commit_id == "ch002"
    assert s.last_commit_chapter() == 2


def test_commit_open_loop_merge_and_resolve(tmp_path: Path) -> None:
    s = ContinuityLedgerStore(tmp_path)
    s.load()
    loop = ContinuityOpenLoop(loop_id="L1", kind="plot", status="open",
                              detail="主线悬念", source_commit_id="ch001")
    s.commit(chapter=1, facts=[], knowledge=[], open_loops=[loop], handoff=_handoff(1))
    # 新增另一条
    s.commit(chapter=2, facts=[], knowledge=[], open_loops=[
        ContinuityOpenLoop(loop_id="L1", kind="plot", status="resolved",
                           detail="主线悬念", source_commit_id="ch002",
                           resolved_in="ch020"),
    ], handoff=_handoff(2))
    assert len(s.ledger.open_loops) == 1  # merge 不重复
    assert s.ledger.open_loops[0].status == "resolved"


def test_handoff_kept_ordered_by_chapter(tmp_path: Path) -> None:
    s = ContinuityLedgerStore(tmp_path)
    s.load()
    # 乱序 commit
    s.commit(chapter=3, facts=[], knowledge=[], open_loops=[], handoff=_handoff(3))
    s.commit(chapter=1, facts=[], knowledge=[], open_loops=[], handoff=_handoff(1))
    chapters = [h.chapter for h in s.ledger.handoffs]
    assert chapters == [1, 3]
    assert s.ledger.latest_handoff().chapter == 3


def test_handoff_commit_replaces_same_chapter(tmp_path: Path) -> None:
    s = ContinuityLedgerStore(tmp_path)
    s.load()
    s.commit(chapter=1, facts=[], knowledge=[], open_loops=[], handoff=_handoff(1))
    s.commit(chapter=1, facts=[], knowledge=[], open_loops=[],
             handoff=ContinuityHandoff(chapter=1, summary="重写", source_commit_id="ch001"))
    assert len(s.ledger.handoffs) == 1
    assert s.ledger.handoffs[0].summary == "重写"


def test_persist_and_reload(tmp_path: Path) -> None:
    s = ContinuityLedgerStore(tmp_path)
    s.load()
    s.commit(
        chapter=1,
        facts=[
            ContinuityFact(domain="world", subject_id="太虚镜", field="status",
                           value="苏醒", source_commit_id="ch001", evidence="e"),
        ],
        knowledge=[
            ContinuityKnowledge(subject_id="太虚镜", audience="reader",
                                audience_id="*", level="knows", source_commit_id="ch001"),
        ],
        open_loops=[], handoff=_handoff(1),
    )
    s2 = ContinuityLedgerStore(tmp_path)
    s2.load()
    assert len(s2.ledger.facts) == 1
    assert s2.ledger.facts[0].value == "苏醒"
    assert len(s2.ledger.knowledge) == 1
    assert s2.ledger.latest_handoff().chapter == 1


def test_corrupted_ledger_degrades_to_empty(tmp_path: Path) -> None:
    f = tmp_path / ".state" / "continuity" / "ledger.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("{ 这不是合法 JSON", encoding="utf-8")
    s = ContinuityLedgerStore(tmp_path)
    s.load()
    assert not s.has_any()
    assert s.last_commit_chapter() is None


# ---------------- 投影有界去重 ----------------
def test_projection_is_latest_view_and_bounded(tmp_path: Path) -> None:
    s = ContinuityLedgerStore(tmp_path)
    s.load()
    for i in range(1, 6):
        s.commit(
            chapter=i,
            facts=[
                ContinuityFact(domain="character", subject_id="林寻", field="realm",
                               value=f"境界{i}", source_commit_id=f"ch{i:03d}", evidence="e"),
            ],
            knowledge=[], open_loops=[], handoff=_handoff(i),
        )
    proj = project(s)
    # 有界：facts 只剩最新态（覆盖去重，不随章数线性增长）
    assert len(proj.facts) == 1
    assert proj.facts[0].value == "境界5"
    assert proj.latest_handoff.chapter == 5


def test_project_to_text_empty_ledger() -> None:
    from agent.core.continuity import project_to_text

    class _Empty:
        def __init__(self) -> None:
            self.ledger = None

    text = project_to_text(project(_Empty()), max_facts=10)
    assert "空账本" in text


def test_project_empty_store() -> None:
    proj = project(None)
    assert proj.facts == [] and proj.latest_handoff is None


# ---------------- 确定性推导 ----------------
def test_derive_and_overdue() -> None:
    loop = ContinuityOpenLoop(
        loop_id="L1", kind="clue", status="open",
        detail="秘密 resolve_after:95", source_commit_id="ch001",
    )
    assert expected_resolve_chapter(loop) == 95
    assert derive_open_loop_status(loop, 100) == "open"  # 未显式闭环 → 维持
    assert is_overdue(loop, 95) is False  # current == expected → 尚不算逾期
    assert is_overdue(loop, 96) is True   # 越过预计回收章 → 逾期


def test_overdue_false_when_resolved() -> None:
    loop = ContinuityOpenLoop(
        loop_id="L1", kind="clue", status="resolved", detail="秘密",
        source_commit_id="ch001", resolved_in="ch095",
    )
    assert is_overdue(loop, 100) is False


def _handoff(chapter: int) -> ContinuityHandoff:
    return ContinuityHandoff(
        chapter=chapter,
        summary=f"第{chapter}章交接",
        source_commit_id=f"ch{chapter:03d}",
        must_carry=["林寻"],
        next_chapter_constraints=["保持紧迫感"],
        open_loops=["L1"],
    )