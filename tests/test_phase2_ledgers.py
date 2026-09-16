"""第二期第二批单测：资源账本 / 新实体引入率 / L2 批间反思（纯离线零 LLM）。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from agent.core.quality.batch_reflection import (
    REFLECTION_FILE,
    build_reflection_input,
    load_latest,
    load_latest_reflection_text,
    record_batch_reflection,
)
from agent.core.story.entity_ledger import EntityLedgerStore
from agent.core.story.intro_rate import intro_rate_text, new_entity_rate
from agent.core.story.resource_ledger import (
    ResourceLedgerStore,
    parse_cn_number,
    sync_resources_from_facts,
)
from tests.conftest import _build_minimal_project


# ---------------------------------------------------------------- 中文数字
@pytest.mark.parametrize("text,expect", [
    ("三十七", 37), ("一百二十", 120), ("两", 2), ("37", 37),
    ("九", 9), ("十", 10), ("十五", 15), ("二百四十", 240), ("零", 0),
])
def test_parse_cn_number_ok(text, expect):
    assert parse_cn_number(text) == expect


@pytest.mark.parametrize("text", ["", "枚", "若干", "一堆"])
def test_parse_cn_number_fail(text):
    assert parse_cn_number(text) is None


# ---------------------------------------------------------------- 资源账本
def _fact(domain, subject, field, value):
    return NS(domain=domain, subject_id=subject, field=field, value=value)


def test_sync_resources_count_and_snapshot(tmp_path: Path) -> None:
    # 首次观察：入账
    n = sync_resources_from_facts(tmp_path, [_fact("world", "培元丹", "count", "三十七")], 210)
    st = ResourceLedgerStore(tmp_path).load()
    e = st.get("培元丹")
    assert e.qty == 37 and e.events[0].desc == "入账 x37"
    # 同值再观察：不重复记事件
    sync_resources_from_facts(tmp_path, [_fact("world", "培元丹", "count", "37")], 211)
    st = ResourceLedgerStore(tmp_path).load()
    assert len(st.get("培元丹").events) == 1
    # 账实不符：以正文为准 + delta 事件
    sync_resources_from_facts(tmp_path, [_fact("world", "培元丹", "count", "二十")], 212)
    st = ResourceLedgerStore(tmp_path).load()
    e = st.get("培元丹")
    assert e.qty == 20 and e.events[-1].delta == -17 and "账实核对" in e.events[-1].desc


def test_sync_resources_count_unparsed_and_holder(tmp_path: Path) -> None:
    sync_resources_from_facts(tmp_path, [_fact("world", "神秘石", "count", "若干")], 5)
    st = ResourceLedgerStore(tmp_path).load()
    assert st.get("神秘石").qty is None  # 解析失败仅登记存在

    sync_resources_from_facts(
        tmp_path, [_fact("world", "青云剑", "holder", "林凡")], 6)
    st = ResourceLedgerStore(tmp_path).load()
    e = st.get("青云剑")
    assert e.holder == "林凡" and "（无） → 林凡" in e.events[-1].desc

    # character 域不入资源账
    sync_resources_from_facts(tmp_path, [_fact("character", "林凡", "count", "三")], 7)
    assert ResourceLedgerStore(tmp_path).load().get("林凡") is None


def test_resource_render_and_roundtrip(tmp_path: Path) -> None:
    sync_resources_from_facts(
        tmp_path,
        [_fact("world", "净脉符", "count", "一百二十"), _fact("world", "净脉符", "holder", "主角")],
        211,
    )
    st = ResourceLedgerStore(tmp_path).load()
    text = st.render_for_prompt(["主角"])
    assert "净脉符（x120）" in text and "不得凭空出现或消失" in text
    assert st.render_for_prompt(["路人"]) == ""
    # 损坏显性
    p = tmp_path / ".state" / "continuity" / "resources.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{broken", encoding="utf-8")
    from agent.core.story.resource_ledger import ResourceError

    with pytest.raises(ResourceError):
        ResourceLedgerStore(tmp_path).load()


# ---------------------------------------------------------------- 引入率
def test_intro_rate_empty(tmp_path: Path) -> None:
    assert new_entity_rate(tmp_path) is None
    assert intro_rate_text(tmp_path) == ""


def test_intro_rate_measures_and_warns(tmp_path: Path) -> None:
    st = EntityLedgerStore(tmp_path).load()
    for i in range(1, 11):
        st.ensure(f"甲{i}", ch=100 + (i % 5))  # 5 章内 10 个 → 均值 2/章
    st.save()

    rate = new_entity_rate(tmp_path, window=5)
    assert rate["total"] == 10 and rate["window_end"] == 104
    # 阈值 1.5 → 告警；阈值 5 → 无告警
    assert "超阈值" in intro_rate_text(tmp_path, window=5, warn_threshold=1.5)
    assert "超阈值" not in intro_rate_text(tmp_path, window=5, warn_threshold=5.0)


def test_intro_rate_in_batch_summary(tmp_path: Path) -> None:
    from agent.workflows.pipeline.batch_replan import build_batch_summary

    st = EntityLedgerStore(tmp_path).load()
    st.ensure("甲", ch=1)
    st.save()
    summary = build_batch_summary(tmp_path)
    assert "新实体引入率" in summary  # 度量段并入复规划摘要


# ---------------------------------------------------------------- L2 批间反思
def test_build_reflection_input_sections(tmp_path: Path) -> None:
    proj = _build_minimal_project(tmp_path)
    (proj / ".state" / "chapter_quality_flags.json").write_text(
        json.dumps({"flags": [{"chapter": 210, "violations": ["命中 AI 腔词句「喃喃自语」（2 次）"]}]},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    inp = build_reflection_input(proj)
    assert "门禁告警留章" in inp and "喃喃自语" in inp


def test_record_reflection_roundtrip(tmp_path: Path) -> None:
    calls = {}

    def chat(messages):
        calls["messages"] = messages
        return {
            "items": [
                {"phenomenon": "第210章 AI 腔命中 2 次", "cause": "deslop:light 覆盖不足",
                 "action": "下一批对含对话章提高质检复核焦点", "verified": False}
            ],
            "summary": "本批质量平稳，AI 腔为唯一复发项",
        }

    assert record_batch_reflection(tmp_path, batch_end_ch=211, chat_fn=chat) is True
    assert "本批生产侧证据" in calls["messages"][1]["content"]

    entry = load_latest(tmp_path)
    assert entry["batch_end_ch"] == 211 and len(entry["items"]) == 1
    text = load_latest_reflection_text(tmp_path)
    assert "作战笔记" in text and "AI 腔命中" in text and "对策" in text

    # 第二次反思：上一批对策进入验证闭环 prompt，历史滚动
    def chat2(messages):
        assert "上一批反思的对策" in messages[1]["content"]
        return {"items": [], "summary": "对策已兑现"}
    record_batch_reflection(tmp_path, batch_end_ch=213, chat_fn=chat2)
    store = json.loads((tmp_path / REFLECTION_FILE).read_text(encoding="utf-8"))
    assert len(store["history"]) == 2 and store["latest"]["summary"] == "对策已兑现"


def test_record_reflection_failure_degrades(tmp_path: Path, caplog) -> None:
    def boom(messages):
        raise RuntimeError("LLM 爆炸")

    import logging

    with caplog.at_level(logging.WARNING):
        assert record_batch_reflection(tmp_path, batch_end_ch=1, chat_fn=boom) is False
    assert any("batch_reflection.record" in (r.getMessage() or "") for r in caplog.records)
    assert not (tmp_path / REFLECTION_FILE).exists()


def test_reflection_in_batch_summary(tmp_path: Path) -> None:
    record_batch_reflection(
        tmp_path, batch_end_ch=211,
        chat_fn=lambda m: {"items": [{"phenomenon": "p", "cause": "c", "action": "a"}],
                           "summary": "s"},
    )
    from agent.workflows.pipeline.batch_replan import build_batch_summary

    assert "作战笔记" in build_batch_summary(tmp_path)


# ---------------------------------------------------------------- 质检合并（同质检查单次调用）
# 2026-09-16：原 `_d_supplement` 合并/关闭两态断言已随废弃写章入口删除
# （登记单 20260916_闸门信号可达性普查 §三.C2）——D 多维审查合并已收敛到
# agentic_write 的写时门禁路径。


# ---------------------------------------------------------------- L1 禁词硬拦截
def test_scan_and_replace_ai_phrases() -> None:
    from agent.workflows.writing.m5_text_hygiene import (
        hard_replace_ai_phrases,
        scan_ai_phrases,
    )

    text = "他喃喃自语，心中一动。她若有所思。"
    assert scan_ai_phrases(text) == {"喃喃自语": 1, "心中一动": 1, "若有所思": 1}
    new, traj = hard_replace_ai_phrases(text)
    assert "喃喃自语" not in new and "心中一动" not in new
    assert "低声说" in new and "沉默片刻" in new
    assert len(traj) == 3
    # 幂等：二次扫描为空
    assert scan_ai_phrases(new) == {}
    assert hard_replace_ai_phrases(new)[1] == []


def test_reflection_input_includes_l1_evidence(tmp_path: Path) -> None:
    # 执行记录 → 门禁回归硬证据（对策闭环）
    proj = _build_minimal_project(tmp_path)
    p = proj / ".state" / "l1_trace.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"ch": 20, "replaced": ["喃喃自语×2→低声说"]}, ensure_ascii=False) + "\n"
        + json.dumps({"ch": 21, "replaced": []}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    inp = build_reflection_input(proj, since_ch=19)
    assert "执行记录" in inp and "喃喃自语×2" in inp and "新增命中 0 处" in inp
    # 时效过滤：since 之后无轨迹则显示零执行
    inp2 = build_reflection_input(proj, since_ch=25)
    assert "本批无替换执行" in inp2
