"""P0-2 事实抽取器回归测试（实体锚定版，2026-09-12）。

背景：五灵破归档写满 185 章、31 次回退。复盘 ``.state/continuity/ledger.json``
发现旧抽取器抽出的全是**量词噪音**且 ``subject_id`` 竟是章节号：

    {"domain":"world","subject_id":"ch179","field":"count","value":"一道",
     "evidence":"脸上的平静才裂开一道缝"}
    {"domain":"world","subject_id":"ch182","field":"count","value":"一根",
     "evidence":"像一根钉入大地的桩"}

账本按 ``(domain, subject_id, field)`` 覆盖更新，拿章节号当主体 = 每章新增噪音行，
写手投影里拿不到任何可消费的状态。本测试把这两个具体缺陷钉成红线：

1. ``subject_id`` 一律是实体名，**绝不允许**是章节号（``ch\\d+``）；
2. 单字名词的量词（一道缝 / 一根桩）不得进账本；
3. 真正的「数量 + 实体名」仍要抽到（三枚镇魔符 → world/镇魔符.count）；
4. 章首的状态变化也要抽到（旧版只看结尾 800 字，章首整段丢失）。
"""

from __future__ import annotations

from agent.workflows.writing.m5_persist import _extract_chapter_facts

_CTX = {
    "characters_info": "- **林砚**：主角，符师\n- **苏晚**：师姐\n- **赵屠**：反派",
}

_CH_NUM_RE = __import__("re").compile(r"^ch\d+$")


def _facts(body: str, ctx: dict | None = None) -> list[tuple[str, str, str, str, str]]:
    raw, _must, _cons = _extract_chapter_facts(7, body, ctx or _CTX)
    return raw


def test_subject_id_is_never_chapter_number() -> None:
    """回归：旧版 subject_id = ch179/ch182，投影全是不可消费的噪音行。"""
    body = (
        "林砚接过镇魔符，脸上的平静才裂开一道缝。"
        "远处像一根钉入大地的桩，封住了山口的灵脉。"
        "他昏迷不醒，苏晚把他扶上马车。"
    )
    facts = _facts(body)
    assert facts, "抽取结果不应为空"
    for _domain, subject, _field, _value, _ev in facts:
        assert not _CH_NUM_RE.match(subject), f"subject_id 不得是章节号：{subject!r}"


def test_quantity_noise_is_dropped() -> None:
    """回归：单字名词的量词（一道缝 / 一根桩）是噪音，不得进账本。"""
    body = "脸上的平静才裂开一道缝。远处像一根钉入大地的桩。"
    facts = _facts(body)
    counts = [f for f in facts if f[2] == "count"]
    assert counts == [], f"量词噪音不应产生 count 事实：{counts}"


def test_count_anchored_to_entity() -> None:
    """真正的「数量 + 实体名」要抽到，主体是实体名而非章号。"""
    body = "林砚取出三枚镇魔符，又喊来三个黑衣人守在门外。"
    facts = _facts(body)
    counts = {f[1]: f[3] for f in facts if f[2] == "count"}
    assert counts.get("镇魔符") == "三枚", counts
    assert counts.get("黑衣人") == "三个", counts


def test_character_state_is_captured() -> None:
    body = "林砚昏迷不醒，苏晚急得直掉眼泪。"
    facts = _facts(body)
    states = {f[1]: f[3] for f in facts if f[2] == "state"}
    assert states.get("林砚") == "昏迷", states


def test_character_location_is_captured() -> None:
    body = "林砚来到藏经阁，抬头看见满墙的符箓。"
    facts = _facts(body)
    locs = {f[1]: f[3] for f in facts if f[2] == "location"}
    assert locs.get("林砚") == "来到藏经阁", locs


def test_item_holder_is_captured() -> None:
    body = "林砚接过镇魔符，苏晚则收好那把青铜钥匙。"
    facts = _facts(body)
    holders = {f[1]: f[3] for f in facts if f[2] == "holder"}
    assert holders.get("镇魔符") == "林砚", holders


def test_head_state_survives_wide_window() -> None:
    """回归：旧版 body[-800:] 只看结尾，章首状态变化整段丢失。"""
    filler = "风声掠过山脊，卷起漫天碎雪。" * 300  # ~4200 字，把章首推出尾 800 字之外
    body = "林砚阵亡了。" + filler + "苏晚独自收剑入鞘。"
    assert len(body) > 4000
    facts = _facts(body)
    states = {f[1]: f[3] for f in facts if f[2] == "state"}
    assert states.get("林砚") == "阵亡", f"章首状态被漏抽：{states}"


def test_negated_state_is_not_a_fact() -> None:
    """「没有离开」不是「离开」——否定句不得沉淀成状态/位移事实。"""
    body = "林砚的手没有离开炉壁，苏晚也未离开药庐。"
    facts = _facts(body)
    assert not [f for f in facts if f[2] == "state"], facts
    assert not [f for f in facts if f[2] == "location"], facts


def test_count_prefers_longer_entity_noun() -> None:
    """量词后应取更具体的实体名（镇灵符 而非 镇灵）。"""
    body = "林砚再出一百张镇灵符，又备下三百张护盾符。"
    counts = {f[1]: f[3] for f in _facts(body) if f[2] == "count"}
    assert counts.get("镇灵符") == "一百张", counts
    assert counts.get("护盾符") == "三百张", counts
    assert "镇灵" not in counts, f"名词被截断成噪音：{counts}"


def test_dialogue_quotes_do_not_create_entities() -> None:
    """ASCII 对白引号成对出现，不得把整句对白切成「实体名」。"""
    from agent.workflows.writing.m5_persist import _entity_names

    body = '他低声道："我知道。"接着又说："能恢复。"'
    names = _entity_names(_CTX, body)
    for junk in ("我知道。", "能恢复。", "我知道", "能恢复"):
        assert junk not in names, f"对白片段被当成实体名：{names}"


def test_holder_strips_modifiers() -> None:
    """持物句要剥掉修饰语，取真正的道具名。"""
    body = "林砚拿起布局图边的一块碎石，随手丢进火里。"
    holders = {f[1]: f[3] for f in _facts(body) if f[2] == "holder"}
    assert holders.get("碎石") == "林砚", holders


def test_colorado_facts_evidence_nonempty() -> None:
    """每条事实都必须带非空 evidence —— 投影 / 体检都要靠它取证。"""
    body = "林砚重伤，苏晚把他背回药庐。"
    for _d, _s, _f, _v, ev in _facts(body):
        assert ev.strip(), "evidence 不得为空"


def test_empty_body_returns_empty() -> None:
    assert _extract_chapter_facts(7, "", _CTX) == ([], [], [])
