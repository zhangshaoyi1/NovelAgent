"""变更准入门 + 心性账本单测（长线一致性设计稿第二期，纯离线零 LLM）。

覆盖：
- ChangeLog：追加/自增 id/驳回必须带理由/查询/最新已批准变更；
- ChangeGate：确定性驳回落日志、批准应用落日志、裁决失明显性降级为附意见批准、
  裁决驳回留痕不应用；
- 心性账本：基线登记、小幅变更直批、跳变>4 无裁决确定性驳回、带裁决批准/驳回、
  事件链记录、写时渲染。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.core.story.change_gate import ProposedChange, propose_change
from agent.core.story.change_log import (
    V_APPROVED,
    V_APPROVED_WARNINGS,
    V_REJECTED,
    ChangeLogError,
    ChangeLogStore,
)
from agent.core.story.disposition_ledger import (
    DISPOSITION_JUMP_REQUIRES_ARBITER,
    DispositionLedgerStore,
    propose_disposition_change,
)
from tests.conftest import _build_minimal_project


# ---------------------------------------------------------------- ChangeLog
def test_change_log_append_and_query(tmp_path: Path) -> None:
    st = ChangeLogStore(tmp_path).load()
    r1 = st.append(_rec(kind="disposition", subject="主角.信任"))
    r2 = st.append(_rec(kind="relation", subject="主角.林惊澜", verdict=V_REJECTED, arbiter_reason="时间线矛盾"))
    st.save()

    st2 = ChangeLogStore(tmp_path).load()
    assert [r.id for r in st2.records] == [r1.id, r2.id]
    assert r1.id == "CHG-0001" and r2.id == "CHG-0002"
    assert len(st2.query(subject="主角.信任")) == 1
    assert len(st2.query(verdict=V_REJECTED)) == 1
    assert st2.latest_applied("主角.信任", "disposition").id == r1.id


def test_change_log_reject_requires_reason(tmp_path: Path) -> None:
    st = ChangeLogStore(tmp_path).load()
    with pytest.raises(ChangeLogError):
        st.append(_rec(verdict=V_REJECTED, arbiter_reason=""))


def test_change_log_requires_reason(tmp_path: Path) -> None:
    st = ChangeLogStore(tmp_path).load()
    with pytest.raises(ChangeLogError):
        st.append(_rec(reason=""))


def _rec(kind="disposition", subject="主角.信任", verdict=V_APPROVED,
         arbiter_reason="", reason="第43章被挚友出卖"):
    from agent.core.story.change_log import ChangeRecord

    return ChangeRecord(id="", kind=kind, subject=subject, before="8/10", after="3/10",
                        reason=reason, chapter=43, decided_by="deterministic",
                        verdict=verdict, arbiter_reason=arbiter_reason)


# ---------------------------------------------------------------- ChangeGate
def test_gate_deterministic_reject_logs(tmp_path: Path) -> None:
    applied: list[str] = []
    v = propose_change(
        tmp_path,
        ProposedChange(kind="setting", subject="境界体系", before="九境", after="七境", reason="想简化"),
        deterministic_check=lambda c: (False, "与设定台账冲突：九境已确立于第5章"),
        apply=lambda c: applied.append(c.subject),
    )
    assert v.verdict == V_REJECTED and not v.applied and not applied
    assert "九境" in v.reason
    log = ChangeLogStore(tmp_path).load()
    assert log.records[0].verdict == V_REJECTED and log.records[0].decided_by == "deterministic"


def test_gate_approve_applies_and_logs(tmp_path: Path) -> None:
    applied: list[str] = []
    v = propose_change(
        tmp_path,
        ProposedChange(kind="setting", subject="X", before="a", after="b", reason="r"),
        apply=lambda c: applied.append(c.subject),
    )
    assert v.verdict == V_APPROVED and v.applied and applied == ["X"]
    assert ChangeLogStore(tmp_path).load().records[0].verdict == V_APPROVED


def test_gate_arbiter_reject_logs_not_applied(tmp_path: Path) -> None:
    applied: list[str] = []
    v = propose_change(
        tmp_path,
        ProposedChange(kind="disposition", subject="主角.信任", before="8", after="3", reason="r"),
        arbiter=lambda c: {"verdict": V_REJECTED, "reason": "转变缺乏事件支撑，第12章他刚被此人所救"},
        apply=lambda c: applied.append(c.subject),
    )
    assert v.verdict == V_REJECTED and not applied
    log = ChangeLogStore(tmp_path).load()
    assert log.records[0].verdict == V_REJECTED
    assert "缺乏事件支撑" in log.records[0].arbiter_reason


def test_gate_arbiter_blind_degrades_to_warnings(tmp_path: Path, caplog) -> None:
    def boom(c):
        raise RuntimeError("裁决 LLM 爆炸")

    applied: list[str] = []
    import logging

    with caplog.at_level(logging.WARNING):
        v = propose_change(
            tmp_path,
            ProposedChange(kind="disposition", subject="s", before="a", after="b", reason="r"),
            arbiter=boom,
            apply=lambda c: applied.append(c.subject),
        )
    assert v.verdict == V_APPROVED_WARNINGS and v.applied
    assert v.warnings and "未经正常裁决" in v.warnings[0]
    assert any("change_gate.arbiter" in (r.getMessage() or "") for r in caplog.records)
    assert ChangeLogStore(tmp_path).load().records[0].verdict == V_APPROVED_WARNINGS


def test_gate_arbiter_unknown_verdict_raises_degrades(tmp_path: Path) -> None:
    applied: list[str] = []
    v = propose_change(
        tmp_path,
        ProposedChange(kind="k", subject="s", before="a", after="b", reason="r"),
        arbiter=lambda c: {"verdict": "perhaps"},
        apply=lambda c: applied.append(c.subject),
    )
    assert v.verdict == V_APPROVED_WARNINGS and v.applied  # 异常路径 → 降级


# ---------------------------------------------------------------- 心性账本
def test_disposition_baseline_and_render(tmp_path: Path) -> None:
    st = DispositionLedgerStore(tmp_path).load()
    st.set_baseline("主角", "对陌生人的信任", 8, note="初入江湖，不设防")
    st.save()

    st2 = DispositionLedgerStore(tmp_path).load()
    text = st2.render_for_prompt(["主角"])
    assert "对陌生人的信任=8/10" in text and "不设防" in text
    assert st2.render_for_prompt(["路人"]) == ""


def test_disposition_small_change_direct_apply(tmp_path: Path) -> None:
    st = DispositionLedgerStore(tmp_path).load()
    st.set_baseline("主角", "对陌生人的信任", 8)
    st.save()

    v = propose_disposition_change(tmp_path, "主角", "对陌生人的信任", 5, "第43章被挚友出卖", 43)
    assert v.verdict == V_APPROVED and v.decided_by == "deterministic"

    st2 = DispositionLedgerStore(tmp_path).load()
    e = st2.get("主角", "对陌生人的信任")
    assert e.value == 5
    assert e.events[-1].from_value == 8 and e.events[-1].to_value == 5
    assert "出卖" in e.events[-1].reason
    # 渲染带最近变化原因
    assert "出卖" in st2.render_for_prompt(["主角"])
    # 元数据落日志
    log = ChangeLogStore(tmp_path).load()
    assert log.latest_applied("主角.对陌生人的信任", "disposition") is not None


def test_disposition_big_jump_requires_arbiter(tmp_path: Path) -> None:
    st = DispositionLedgerStore(tmp_path).load()
    st.set_baseline("主角", "对陌生人的信任", 8)
    st.save()

    v = propose_disposition_change(tmp_path, "主角", "对陌生人的信任", 2, "突然就不信人了", 50)
    assert v.verdict == V_REJECTED
    assert str(DISPOSITION_JUMP_REQUIRES_ARBITER) in v.reason
    # 账本不变，日志留痕
    assert DispositionLedgerStore(tmp_path).load().get("主角", "对陌生人的信任").value == 8
    assert ChangeLogStore(tmp_path).load().records[0].verdict == V_REJECTED


def test_disposition_big_jump_with_arbiter(tmp_path: Path) -> None:
    st = DispositionLedgerStore(tmp_path).load()
    st.set_baseline("主角", "对陌生人的信任", 8)
    st.save()

    calls = {}

    def arbiter(c):
        calls["before"] = c.before
        return {"verdict": V_APPROVED, "reason": "三段伏笔支撑：43章被刺/67章灭门/89章恩人反目"}

    v = propose_disposition_change(tmp_path, "主角", "对陌生人的信任", 2, "连环背叛后心性大变", 90, arbiter=arbiter)
    assert v.verdict == V_APPROVED and calls["before"] == "8/10"
    assert DispositionLedgerStore(tmp_path).load().get("主角", "对陌生人的信任").value == 2


def test_disposition_big_jump_arbiter_reject_keeps_state(tmp_path: Path) -> None:
    st = DispositionLedgerStore(tmp_path).load()
    st.set_baseline("主角", "克制力", 3)
    st.save()

    v = propose_disposition_change(
        tmp_path, "主角", "克制力", 9, "想让他突然成熟", 60,
        arbiter=lambda c: {"verdict": V_REJECTED, "reason": "缺少成长事件链，60章前无磨砺情节"},
    )
    assert v.verdict == V_REJECTED
    assert DispositionLedgerStore(tmp_path).load().get("主角", "克制力").value == 3


def test_disposition_invalid_value_rejected(tmp_path: Path) -> None:
    v = propose_disposition_change(tmp_path, "主角", "克制力", 15, "越界测试", 1)
    assert v.verdict == V_REJECTED and "0-10" in v.reason


# ---------------------------------------------------------------- 写时注入
def test_ledger_context_includes_disposition(tmp_path: Path) -> None:
    from agent.workflows.writing.m5_write_chapter import M5WriteChapterWorkflow
    from agent.core.story.setting_manager import SettingManager

    proj = _build_minimal_project(tmp_path)
    st = DispositionLedgerStore(proj).load()
    st.set_baseline("主角", "对陌生人的信任", 3, note="被刺后多疑")
    st.save()

    wf = M5WriteChapterWorkflow(proj, llm_client=None, pre_validate=False)
    sublines = SettingManager(proj).list_sublines()
    text = wf._load_ledger_context(wf.sm.load_subline(sublines[0]), 10)
    assert "心性轨迹" in text and "对陌生人的信任=3/10" in text and "被刺后多疑" in text
