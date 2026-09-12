"""实体名册 / 叙事线 / 信息账本 / 战力标尺账单测（第一期骨架）。"""

from __future__ import annotations

import json

import pytest

from agent.core.story.entity_ledger import (
    LC_CLOSED,
    LC_RETIRED,
    READER_HOLDER,
    EntityLedgerError,
    EntityLedgerStore,
    KnowledgeLedgerStore,
    PowerScaleLedgerStore,
    render_ledger_context,
)


# ---------------------------------------------------------------- 实体名册
def test_ensure_registers_and_records_appearances(tmp_path):
    st = EntityLedgerStore(tmp_path).load()
    e1 = st.ensure("王铁柱", ch=3)
    e2 = st.ensure("王铁柱", ch=7)
    assert e1 is e2
    assert e1.first_ch == 3 and e1.last_ch == 7
    assert e1.appearances == [3, 7]
    assert e1.lifecycle == "accompanying"
    # 同章去重
    st.ensure("王铁柱", ch=7)
    assert e1.appearances == [3, 7]


def test_ensure_empty_name_raises(tmp_path):
    with pytest.raises(EntityLedgerError):
        EntityLedgerStore(tmp_path).ensure("  ")


def test_motivation_and_obligations_roundtrip(tmp_path):
    st = EntityLedgerStore(tmp_path).load()
    st.ensure("黑袍人", ch=5)
    st.set_motivation("黑袍人", desire="重开魔渊", origin_event="宗门灭门之恨", logic="借传承布局")
    st.add_obligation("黑袍人", "身世之谜待揭示", ch=5)
    st.save()

    st2 = EntityLedgerStore(tmp_path).load()
    e = st2.get("黑袍人")
    assert e.motivation.desire == "重开魔渊"
    assert e.open_obligations()[0].text == "身世之谜待揭示"


def test_set_motivation_unknown_entity_raises(tmp_path):
    with pytest.raises(EntityLedgerError):
        EntityLedgerStore(tmp_path).set_motivation("不存在", desire="x")


def test_dormant_scan(tmp_path):
    st = EntityLedgerStore(tmp_path).load()
    st.ensure("甲", ch=1)
    st.add_obligation("甲", "未回收的约定", ch=1)
    st.ensure("乙", ch=95)
    st.add_obligation("乙", "近期义务", ch=95)
    st.ensure("丙", ch=1)  # 无义务，不告警
    dorm = st.dormant_entities(current_ch=100, threshold=20)
    assert [e.name for e in dorm] == ["甲"]


def test_lifecycle_closed_excluded_from_dormant(tmp_path):
    st = EntityLedgerStore(tmp_path).load()
    st.ensure("甲", ch=1)
    st.add_obligation("甲", "x", ch=1)
    st.set_lifecycle("甲", LC_RETIRED)
    assert st.dormant_entities(current_ch=100) == []
    with pytest.raises(EntityLedgerError):
        st.set_lifecycle("甲", "bogus")


# ---------------------------------------------------------------- 叙事线
def test_thread_add_advance_close(tmp_path):
    st = EntityLedgerStore(tmp_path).load()
    t = st.add_thread("青云传承线", bound_entity="青阳子", urgency="high")
    assert t.id == "THREAD-001"
    assert st.advance_thread(t.id, 12, "主角于青云秘境得第一道传承")
    assert st.advance_thread(t.id, 40, "第二道传承")
    st.save()

    st2 = EntityLedgerStore(tmp_path).load()
    t2 = st2.threads[0]
    assert len(t2.milestones) == 2
    assert len(st2.open_threads()) == 1
    assert st2.close_thread(t2.id)
    assert st2.open_threads() == []
    assert not st2.close_thread("THREAD-999")


def test_thread_invalid_urgency(tmp_path):
    with pytest.raises(EntityLedgerError):
        EntityLedgerStore(tmp_path).add_thread("x", urgency="urgent")


# ---------------------------------------------------------------- 写时渲染
def test_render_for_prompt_sections(tmp_path):
    st = EntityLedgerStore(tmp_path).load()
    st.ensure("王铁柱", ch=3)
    st.set_motivation("王铁柱", desire="复仇")
    st.add_obligation("王铁柱", "杀仇人", ch=3)
    st.add_thread("传承线", bound_entity="老怪")
    st.ensure("隐士", ch=1)
    st.add_obligation("隐士", "承诺未兑现", ch=1)

    text = st.render_for_prompt(["王铁柱", "路人甲"], current_ch=50)
    assert "王铁柱" in text and "复仇" in text and "杀仇人" in text
    assert "传承线" in text
    assert "休眠预警" in text and "隐士" in text
    assert "路人甲" not in text  # 未登记实体不渲染


def test_render_empty_returns_empty(tmp_path):
    assert EntityLedgerStore(tmp_path).load().render_for_prompt(["无名"], current_ch=9) == ""


def test_corrupt_file_raises(tmp_path):
    p = tmp_path / ".state" / "continuity" / "entity_ledger.json"
    p.parent.mkdir(parents=True)
    p.write_text("{broken", encoding="utf-8")
    with pytest.raises(EntityLedgerError):
        EntityLedgerStore(tmp_path).load()


# ---------------------------------------------------------------- 信息账本
def test_knowledge_record_render(tmp_path):
    st = KnowledgeLedgerStore(tmp_path).load()
    st.record("主角", "幕后黑手是掌门", learned_ch=30, source="第30章密室")
    st.record(READER_HOLDER, "掌门早已知道主角身世", learned_ch=25)
    st.save()

    st2 = KnowledgeLedgerStore(tmp_path).load()
    text = st2.render_for_prompt(["主角", "路人"])
    assert "幕后黑手是掌门" in text
    assert "读者已知" in text and "掌门早已知道主角身世" in text
    assert "路人" not in text


def test_knowledge_requires_holder_and_fact(tmp_path):
    with pytest.raises(EntityLedgerError):
        KnowledgeLedgerStore(tmp_path).record(" ", "fact")


# ---------------------------------------------------------------- 战力标尺账
def test_power_scale_roundtrip_and_benchmark_replace(tmp_path):
    st = PowerScaleLedgerStore(tmp_path).load()
    st.record_progress(10, "突破筑基")
    st.set_benchmark("金丹", "第12章一掌灭青云宗", 12)
    st.set_benchmark("金丹", "第88章金丹老怪被主角三招击退", 88)  # 同层覆盖
    st.save()

    st2 = PowerScaleLedgerStore(tmp_path).load()
    assert st2.progress == [{"ch": 10, "desc": "突破筑基"}]
    assert len(st2.benchmarks) == 1
    assert "三招击退" in st2.benchmarks[0].reference
    text = st2.render_for_prompt()
    assert "突破筑基" in text and "战力参照物" in text


# ---------------------------------------------------------------- 聚合口
def test_render_ledger_context_merges_and_degrades(tmp_path, caplog):
    # 空账本 → 空串；损坏 → degrade 降级为空（不抛）
    assert render_ledger_context(tmp_path, ["主角"], current_ch=1) == ""

    p = tmp_path / ".state" / "continuity" / "entity_ledger.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{broken", encoding="utf-8")
    import logging

    with caplog.at_level(logging.WARNING):
        out = render_ledger_context(tmp_path, ["主角"], current_ch=1)
    assert out == ""
    assert any("entity_ledger.render" in (r.getMessage() or "") for r in caplog.records)


def test_render_ledger_context_full(tmp_path):
    est = EntityLedgerStore(tmp_path).load()
    est.ensure("甲", ch=1)
    est.save()
    kst = KnowledgeLedgerStore(tmp_path).load()
    kst.record("主角", "知道A秘密")
    kst.save()
    pst = PowerScaleLedgerStore(tmp_path).load()
    pst.record_progress(2, "得剑")
    pst.save()

    out = render_ledger_context(tmp_path, ["甲", "主角"], current_ch=3)
    assert "实体名册摘要" in out and "信息账本" in out and "战力标尺账" in out
    assert "知道A秘密" in out


# ---------------------------------------------------------------- 自动登记同步
from types import SimpleNamespace as _NS


def test_sync_entities_from_facts_registers_and_retires(tmp_path):
    from agent.core.story.entity_ledger import sync_entities_from_facts

    facts = [
        _NS(domain="character", subject_id="王铁柱", field="presence", value=f"第9章在场"),
        _NS(domain="character", subject_id="老盟主", field="state", value="陨落"),
        _NS(domain="character", subject_id="", field="state", value="死"),  # 空主体跳过
        _NS(domain="world", subject_id="青云剑", field="holder", value="王铁柱"),
        _NS(domain="world", subject_id="青云剑", field="count", value="三枚"),  # 非holder不登记
    ]
    n = sync_entities_from_facts(tmp_path, facts, 9)
    assert n == 3  # 王铁柱 + 老盟主 + 持有者王铁柱（已存在仍计）

    st = EntityLedgerStore(tmp_path).load()
    assert st.get("王铁柱").appearances == [9]
    assert st.get("老盟主").lifecycle == "retired"
    assert st.get("青云剑") is None  # 道具本体不入册（char/faction/place 之外）


def test_sync_entities_from_facts_empty_no_save(tmp_path):
    from agent.core.story.entity_ledger import sync_entities_from_facts

    assert sync_entities_from_facts(tmp_path, [], 9) == 0
    assert not (tmp_path / ".state" / "continuity" / "entity_ledger.json").exists()


# ---------------------------------------------------------------- commit id 双风格兼容（2026-09-13 实弹发现）
def test_commit_id_matches_both_styles():
    from agent.core.continuity.ledger import commit_id_matches

    assert commit_id_matches("5", 5)
    assert commit_id_matches("ch5", 5)
    assert commit_id_matches("ch005", 5)
    assert not commit_id_matches("ch006", 5)
    assert not commit_id_matches("6", 5)
    assert not commit_id_matches("", 5)


def test_backfill_real_project_numeric_commits(tmp_path):
    # 回归：裸章号 commit 的 character facts 能被过滤器命中并登记名册
    from agent.core.story.entity_ledger import sync_entities_from_facts

    facts = [
        _NS(domain="character", subject_id="林凡", field="status", value="外门弟子"),
        _NS(domain="character", subject_id="小工", field="alive", value="自主动作"),
        _NS(domain="world", subject_id="培元丹", field="count", value="十"),
    ]
    assert sync_entities_from_facts(tmp_path, facts, 5) >= 2
    st = EntityLedgerStore(tmp_path).load()
    assert st.get("林凡") and st.get("小工")
