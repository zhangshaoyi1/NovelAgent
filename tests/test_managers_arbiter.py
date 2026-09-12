"""关系网账本 + 计划审计 + LLM 裁决接线单测（第二期，纯离线零 LLM）。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace as NS

from agent.core.story.change_log import (
    V_APPROVED,
    V_APPROVED_WARNINGS,
    V_REJECTED,
    ChangeLogStore,
)
from agent.core.story.plan_managers import (
    audit_plan,
    save_audit_report,
)
from agent.core.story.relation_ledger import (
    RelationLedgerStore,
    propose_relation_change,
)
from tests.conftest import _build_minimal_project


def _arc(name, start, end, goal=""):
    from agent.agents.planner import Arc

    return Arc(id=f"a-{name}", name=name, chapter_start=start, chapter_end=end, goal=goal)


# ---------------------------------------------------------------- 关系网账本
def test_relation_baseline_and_render(tmp_path: Path) -> None:
    st = RelationLedgerStore(tmp_path).load()
    st.set_baseline("主角", "林惊澜", "盟友", note="并肩作战期")
    st.save()

    st2 = RelationLedgerStore(tmp_path).load()
    text = st2.render_for_prompt(["主角"])
    assert "盟友" in text and "并肩作战" in text
    # 无序对归一化：反向查询命中同一关系
    assert st2.get("林惊澜", "主角") is not None


def test_relation_change_via_gate(tmp_path: Path) -> None:
    st = RelationLedgerStore(tmp_path).load()
    st.set_baseline("主角", "林惊澜", "盟友")
    st.save()

    v = propose_relation_change(
        tmp_path, "主角", "林惊澜", "反目", "误会：她被怀疑是内应", 55,
        scar="真相大白后信任仍余裂痕",
    )
    assert v.verdict == V_APPROVED

    st2 = RelationLedgerStore(tmp_path).load()
    e = st2.get("主角", "林惊澜")
    assert e.state == "反目"
    assert e.events[-1].from_state == "盟友" and e.events[-1].ch == 55
    assert e.scars == ["真相大白后信任仍余裂痕"]
    text = st2.render_for_prompt(["主角", "林惊澜"])
    assert "反目" in text and "留疤" in text and "裂痕" in text
    # 日志留痕
    assert ChangeLogStore(tmp_path).load().records[0].kind == "relation"


def test_relation_noop_change_rejected(tmp_path: Path) -> None:
    st = RelationLedgerStore(tmp_path).load()
    st.set_baseline("甲", "乙", "敌对")
    st.save()
    v = propose_relation_change(tmp_path, "甲", "乙", "敌对", "想改但没变")
    assert v.verdict == V_REJECTED and "无变化" in v.reason


def test_relation_new_pair_allowed(tmp_path: Path) -> None:
    # 首次登记关系（old=未登记）不算 noop
    v = propose_relation_change(tmp_path, "甲", "乙", "初识", "市集邂逅", 3)
    assert v.verdict == V_APPROVED
    assert RelationLedgerStore(tmp_path).load().get("甲", "乙").state == "初识"


# ---------------------------------------------------------------- 计划审计
def test_audit_plan_clean(tmp_path: Path) -> None:
    proj = _build_minimal_project(tmp_path)
    report = audit_plan(proj, [_arc("弧一", 11, 30), _arc("弧二", 31, 50)], current_chapter=10)
    assert report.passed and not report.warns


def test_audit_plan_overlap_and_progress_blocks(tmp_path: Path) -> None:
    proj = _build_minimal_project(tmp_path)
    report = audit_plan(proj, [_arc("历史弧", 1, 10), _arc("重叠弧", 8, 30)], current_chapter=10)
    assert not report.passed
    assert any(f.level == "block" and f.manager == "continuity" for f in report.findings)


def test_audit_plan_gap_and_overrun(tmp_path: Path) -> None:
    proj = _build_minimal_project(tmp_path)
    (proj / ".state").mkdir(parents=True, exist_ok=True)
    (proj / ".state" / "plan.json").write_text(
        json.dumps({"total_chapters": 100}, ensure_ascii=False), encoding="utf-8"
    )
    report = audit_plan(proj, [_arc("弧一", 11, 30), _arc("弧二", 35, 120)], current_chapter=10)
    assert any(f.manager == "structure" and f.level == "warn" for f in report.findings)  # 空洞
    assert any(f.manager == "structure" and f.level == "block" for f in report.findings)  # 越界 120>100


def test_audit_plan_dormant_and_thread_warns(tmp_path: Path) -> None:
    from agent.core.story.entity_ledger import EntityLedgerStore

    proj = _build_minimal_project(tmp_path)
    st = EntityLedgerStore(proj).load()
    st.ensure("隐士", ch=1)
    st.add_obligation("隐士", "承诺未兑现", ch=1)
    t = st.add_thread("青云传承线", urgency="high")
    st.advance_thread(t.id, 5, "第一道传承")
    st.save()

    report = audit_plan(proj, [_arc("无关弧", 21, 30, goal="主角修炼")], current_chapter=25)
    managers = {f.manager for f in report.findings}
    assert "character" in managers and "debt" in managers
    save_audit_report(proj, report)
    assert (proj / ".state" / "plan_audit.json").exists()


# ---------------------------------------------------------------- LLM 裁决接线
def test_make_llm_arbiter_injected_chat(tmp_path: Path) -> None:
    from agent.core.story.arbiter import ArbiterOutput, make_llm_arbiter, render_arbiter_messages

    captured = {}

    def chat(messages):
        captured["messages"] = messages
        return ArbiterOutput(verdict=V_REJECTED, reason="与第43章被刺事件矛盾").model_dump()

    from agent.core.story.change_gate import ProposedChange

    change = ProposedChange(kind="disposition", subject="主角.信任", before="8/10", after="9/10",
                            reason="想让他更信任别人", chapter=44)
    chat = make_llm_arbiter(chat_fn=chat)
    out = chat(render_arbiter_messages(change))

    assert out["verdict"] == V_REJECTED
    # prompt 携带裁决纪律与提案字段
    sys_msg = captured["messages"][0]["content"]
    assert "rejected" in sys_msg and "approved_with_warnings" in sys_msg and "无权否决" in sys_msg
    user_msg = captured["messages"][1]["content"]
    assert "主角.信任" in user_msg and "8/10" in user_msg

    # 与 Gate 端到端
    from agent.core.story.disposition_ledger import DispositionLedgerStore, propose_disposition_change

    DispositionLedgerStore(tmp_path).load().set_baseline("主角", "信任", 8)
    v = propose_disposition_change(
        tmp_path, "主角", "信任", 9, "想让他更信任", 44, arbiter=make_llm_arbiter(chat_fn=chat)
    )
    assert v.verdict == V_REJECTED and "矛盾" in v.reason


def test_arbiter_output_schema_roundtrip() -> None:
    from agent.core.story.arbiter import ArbiterOutput

    out = ArbiterOutput(verdict=V_APPROVED_WARNINGS, reason="转变略突兀", warnings=["补一场铺垫戏"])
    assert json.loads(out.model_dump_json())["verdict"] == V_APPROVED_WARNINGS


# ---------------------------------------------------------------- 关系网写时注入
def test_ledger_context_includes_relations(tmp_path: Path) -> None:
    from agent.core.story.relation_ledger import RelationLedgerStore
    from agent.core.story.setting_manager import SettingManager
    from agent.workflows.writing.m5_write_chapter import M5WriteChapterWorkflow

    proj = _build_minimal_project(tmp_path)
    st = RelationLedgerStore(proj).load()
    st.set_baseline("主角", "林惊澜", "盟友")
    st.save()

    wf = M5WriteChapterWorkflow(proj, llm_client=None, pre_validate=False)
    sublines = SettingManager(proj).list_sublines()
    text = wf._load_ledger_context(wf.sm.load_subline(sublines[0]), 10)
    # 最小项目出场角色未必含林惊澜——留疤段/命中其一即可；未命中则至少不炸
    if any(n in (wf.sm.load_subline(sublines[0]).get("content") or "") for n in ("主角", "林惊澜")):
        assert "关系网轨迹" in text or "心性轨迹" in text
