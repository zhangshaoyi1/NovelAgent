"""P1-5 连续性账本结构化 delta 结算（core/continuity/delta.py + ledger.apply_delta）。

覆盖：
- LedgerDelta / LoopOp 严格校验：未知字段拒绝、resolve 必带 resolved_in、
  abandon 必带原因；
- apply_delta 正常路径：facts 覆盖/新增、knowledge 覆盖、loop 状态机推进、handoff 覆盖；
- 幂等重放：同一 delta 应用两次结果一致；
- 失败显式：目标 loop 不存在 → LedgerDeltaError，且账本保持原样（无半应用）、不落盘；
- store.apply_delta 端到端：落盘生效、重载后一致、证据链锚收口为本 commit。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent.core.continuity.delta import LedgerDelta, LedgerDeltaError, LoopOp
from agent.core.continuity.ledger import ContinuityLedgerStore
from agent.core.continuity.models import (
    ContinuityFact,
    ContinuityHandoff,
    ContinuityKnowledge,
    ContinuityOpenLoop,
    ContinuityLedger,
)


def _fact(subject: str = "林寻", field: str = "status", value: str = "断臂", chapter: int = 5) -> ContinuityFact:
    return ContinuityFact(
        domain="character", subject_id=subject, field=field, value=value,
        source_commit_id=str(chapter), evidence="第五章正文",
    )


def _loop(loop_id: str = "L-01", status: str = "open") -> ContinuityOpenLoop:
    return ContinuityOpenLoop(
        loop_id=loop_id, kind="plot", status=status,  # type: ignore[arg-type]
        detail="黑匣下落", source_commit_id="1",
    )


# ---------------------------------------------------------------- schema 校验
def test_loop_op_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        LoopOp.model_validate({
            "op": "resolve", "loop_id": "L-01", "resolved_in": "ch010",
            "source_commit_id": "10", "hallucinated_field": "x",  # 未知字段
        })


def test_ledger_delta_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        LedgerDelta.model_validate({"chapter": 5, "oops": 1})


def test_resolve_requires_resolved_in_and_abandon_requires_reason() -> None:
    with pytest.raises(ValidationError, match="resolved_in"):
        LoopOp(op="resolve", loop_id="L-01", source_commit_id="10")
    with pytest.raises(ValidationError, match="detail"):
        LoopOp(op="abandon", loop_id="L-01", source_commit_id="10")


# ---------------------------------------------------------------- 应用语义
def _ledger_with_loop() -> ContinuityLedger:
    return ContinuityLedger(open_loops=[_loop()], facts=[_fact(value="旧值", chapter=1)])


def test_apply_delta_normal_path() -> None:
    ledger = _ledger_with_loop()
    delta = LedgerDelta(
        chapter=5,
        facts=[_fact()],  # 覆盖 (character, 林寻, status)
        knowledge=[
            ContinuityKnowledge(
                subject_id="林寻", audience="reader", audience_id="*",
                level="knows", source_commit_id="5",
            )
        ],
        loop_ops=[LoopOp(op="resolve", loop_id="L-01", resolved_in="ch005", source_commit_id="5")],
        handoff=ContinuityHandoff(
            chapter=5, summary="断臂", source_commit_id="5",
        ),
    )
    cid = apply(ledger, delta)
    assert cid == "5"
    assert ledger.facts[0].value == "断臂"
    assert ledger.facts[0].source_commit_id == "5"  # 证据链锚收口为本 commit
    assert ledger.knowledge[0].level == "knows"
    assert ledger.open_loops[0].status == "resolved"
    assert ledger.open_loops[0].resolved_in == "ch005"
    assert ledger.latest_handoff().summary == "断臂"


def apply(ledger: ContinuityLedger, delta: LedgerDelta) -> str:
    from agent.core.continuity.delta import apply_ledger_delta

    return apply_ledger_delta(ledger, delta)


def test_apply_delta_idempotent_replay() -> None:
    ledger = _ledger_with_loop()
    delta = LedgerDelta(
        chapter=5,
        facts=[_fact()],
        loop_ops=[LoopOp(op="advance", loop_id="L-01", detail="线索出现", source_commit_id="5")],
        handoff=ContinuityHandoff(chapter=5, summary="s", source_commit_id="5"),
    )
    apply(ledger, delta)
    snap1 = ledger.model_dump(mode="json")
    apply(ledger, delta)  # 幂等重放
    snap2 = ledger.model_dump(mode="json")
    assert snap1 == snap2


def test_apply_delta_defer_keeps_open_and_progressing_never_regresses() -> None:
    ledger = _ledger_with_loop()
    apply(ledger, LedgerDelta(
        chapter=5,
        loop_ops=[LoopOp(op="advance", loop_id="L-01", detail="推进", source_commit_id="5")],
    ))
    assert ledger.open_loops[0].status == "progressing"
    # defer 不把 progressing 打回 open
    apply(ledger, LedgerDelta(
        chapter=6,
        loop_ops=[LoopOp(op="defer", loop_id="L-01", detail="本章先不揭", source_commit_id="6")],
    ))
    assert ledger.open_loops[0].status == "progressing"


def test_apply_delta_missing_loop_target_no_half_apply() -> None:
    ledger = _ledger_with_loop()
    bad = LedgerDelta(
        chapter=5,
        facts=[_fact()],  # 若先应用 facts 再失败就是半应用
        loop_ops=[LoopOp(op="resolve", loop_id="L-999", resolved_in="ch005", source_commit_id="5")],
    )
    with pytest.raises(LedgerDeltaError, match="L-999"):
        apply(ledger, bad)
    # 账本保持原样（预检先行，杜绝半应用）
    assert ledger.facts[0].value == "旧值"


def test_terminal_state_never_regresses() -> None:
    ledger = _ledger_with_loop()
    ledger.open_loops[0].status = "resolved"
    apply(ledger, LedgerDelta(
        chapter=6,
        loop_ops=[LoopOp(op="advance", loop_id="L-01", detail="试图复活已闭环线", source_commit_id="6")],
    ))
    assert ledger.open_loops[0].status == "resolved"


# ---------------------------------------------------------------- 新增剧情线同 delta 先登记再操作
def test_apply_delta_register_then_resolve_in_same_delta() -> None:
    """同一 delta 可先登记新开环（open_loops），再对其做操作——闭环"从未登记却要 resolve"缺口。"""
    ledger = ContinuityLedger(open_loops=[], facts=[])  # 空账本
    delta = LedgerDelta(
        chapter=20,
        open_loops=[_loop(loop_id="傀儡制作")],  # 本 delta 先登记新线
        loop_ops=[LoopOp(op="resolve", loop_id="傀儡制作", resolved_in="ch020", source_commit_id="20")],
    )
    cid = apply(ledger, delta)
    assert cid == "20"
    assert [lo.loop_id for lo in ledger.open_loops] == ["傀儡制作"]
    assert ledger.open_loops[0].status == "resolved"
    assert ledger.open_loops[0].source_commit_id == "20"  # 证据链锚收口


def test_apply_delta_open_loops_anchors_to_commit_and_dedups() -> None:
    ledger = ContinuityLedger(open_loops=[_loop(loop_id="L-01")])
    apply(ledger, LedgerDelta(
        chapter=20,
        open_loops=[_loop(loop_id="L-01", status="progressing")],  # 同 id 覆盖
        loop_ops=[],
    ))
    assert len(ledger.open_loops) == 1  # 不重复追加
    assert ledger.open_loops[0].status == "progressing"
    assert ledger.open_loops[0].source_commit_id == "20"


def test_apply_delta_register_does_not_poison_missing_op_target_check() -> None:
    """open_loops 只登记 N 条，操作 N 条以外的 id 仍必须失败。"""
    ledger = ContinuityLedger(open_loops=[])
    with pytest.raises(LedgerDeltaError, match="傀儡制作"):
        apply(ledger, LedgerDelta(
            chapter=20,
            loop_ops=[LoopOp(op="resolve", loop_id="傀儡制作", resolved_in="ch020", source_commit_id="20")],
        ))
    assert ledger.open_loops == []  # 未登记、账本未改


# ---------------------------------------------------------------- store 端到端
def test_store_apply_delta_persists_and_reloads(tmp_path: Path) -> None:
    store = ContinuityLedgerStore(tmp_path)
    store.load()
    store.ledger = ContinuityLedger(open_loops=[_loop()])
    store.save()

    store2 = ContinuityLedgerStore(tmp_path)
    store2.load()
    cid = store2.apply_delta(LedgerDelta(
        chapter=5,
        facts=[_fact()],
        loop_ops=[LoopOp(op="resolve", loop_id="L-01", resolved_in="ch005", source_commit_id="5")],
        handoff=ContinuityHandoff(chapter=5, summary="断臂", source_commit_id="5"),
    ))
    assert cid == "5"

    # 重载后一致（落盘生效）
    store3 = ContinuityLedgerStore(tmp_path)
    ledger = store3.load()
    assert ledger.facts[0].value == "断臂"
    assert ledger.open_loops[0].status == "resolved"
    data = json.loads(store3.file.read_text(encoding="utf-8"))
    assert data["handoffs"][0]["chapter"] == 5


def test_store_apply_delta_failure_leaves_disk_untouched(tmp_path: Path) -> None:
    store = ContinuityLedgerStore(tmp_path)
    store.load()
    store.ledger = ContinuityLedger(open_loops=[_loop()])
    store.save()
    before = store.file.read_text(encoding="utf-8")

    store2 = ContinuityLedgerStore(tmp_path)
    store2.load()
    with pytest.raises(LedgerDeltaError):
        store2.apply_delta(LedgerDelta(
            chapter=5,
            loop_ops=[LoopOp(op="resolve", loop_id="L-404", resolved_in="x", source_commit_id="5")],
        ))
    assert store.file.read_text(encoding="utf-8") == before  # 磁盘未被污染
