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


# ============================================================
# 切错片段守卫（2026-09-25 灵荒工坊 ch47 判定事故回归）
# ============================================================
def test_simile_span_is_not_an_entity() -> None:
    """回归：比况片段（「X 字像 Y 一样」）不得成实体。

    旧表只挡虚词，于是「『属性对冲』四个字像烙印一样刻在他记忆里」被抽成
    ``world/字像烙印.count=四个``；评委据此把 ch51 的「符号出现在三个地方，三种标记」
    判成**数量矛盾**（``logic_holes`` 硬指标不达标 ⇒ 整轮 escalated）。
    """
    body = "他脑子里还是那本册子上的字，「属性对冲」四个字像烙印一样刻在他记忆里。"
    counts = [f for f in _facts(body) if f[2] == "count"]
    assert counts == [], f"比况片段不是实体名：{counts}"


def test_verb_phrase_after_measure_word_is_not_an_entity() -> None:
    """回归：动词 / 能愿动词 / 副词起头的片段不是实体名（旧版抽出一批脏实体）。"""
    cases = {
        "一个身穿灰袍的中年人站在门口。": "身穿灰袍",
        "一个数字钉死了整条线。": "数字钉",
        "二层已解锁。": "已解锁",
        "一个能翻盘的机会摆在眼前。": "能翻盘",
    }
    for body, bad in cases.items():
        counts = {f[1] for f in _facts(body) if f[2] == "count"}
        assert bad not in counts, f"{bad} 是切错片段，不该进账本：{counts}"


def test_truncated_noun_is_not_an_entity() -> None:
    """回归：名词被切短成「…傀」不得进账本（傀 须与 儡 连用，单独收尾即截断）。"""
    body = "林砚推出十二具标准化傀儡，又拖来一具粗劣傀儡。"
    counts = {f[1] for f in _facts(body) if f[2] == "count"}
    assert not any(c.endswith("傀") for c in counts), f"截断名词进账本了：{counts}"


def test_count_boundary_keeps_legit_compound_nouns() -> None:
    """边界锁定：新增禁用字不得误伤正常实体名。

    实测反例：``劣`` 曾被收进禁用字，于是「三枚劣品灵石」这条**正当**计数事实被
    一并挡掉（灵荒工坊 ch014）——凡有反例的字一律不得进 ``_NOUN_BAD``。
    """
    body = "林砚取出一块完整灵石，又把五枚五行石排在桌上，三个外门弟子还在门外。"
    counts = {f[1]: f[3] for f in _facts(body) if f[2] == "count"}
    assert counts.get("完整灵石") == "一块", counts
    assert counts.get("五行石") == "五枚", counts
    assert counts.get("外门弟子") == "三个", counts

    legit = "林砚把三枚劣品灵石收进布袋。"
    counts2 = {f[1]: f[3] for f in _facts(legit) if f[2] == "count"}
    assert counts2.get("劣品灵石") == "三枚", counts2


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


# ============================================================
# 设计维度事实（2026-09-16）——「除了性格之外，别的都不是一成不变的」
# ============================================================
# 背景（登记单 20260916_角色弧光规格未建立与设计内转变被误判）：
# 旧抽取器只有 presence/state/location/holder/count 五类 ⇒ 境界提升、关系演变、
# 心性转变这些"设计上明确要求推进"的维度**从不落盘** ⇒ 后续章节永远拿初始档案
# 说话（"已是铸府期"仍按杂役写），且评委据此把设计内推进判成前后矛盾 ⇒ 整窗回退。
# 下面四条把「设计维度必须结账」钉成红线。


def test_realm_advance_is_captured() -> None:
    """境界推进要落盘为 character/<名>.realm —— 设计上「境界：杂役→归一期」。"""
    body = "林砚闭关三月，终于突破到栖气期，神识也随之大涨。"
    facts = _facts(body)
    realms = {s: v for _d, s, f, v, _e in facts if f == "realm"}
    assert realms.get("林砚") == "栖气期", facts


def test_relation_change_is_captured() -> None:
    """关系演变要落盘为 character/<名>.relation —— 档案「关系」段登记的是演变方向。"""
    body = "此战之后，林砚与苏晚正式结盟。"
    facts = _facts(body)
    rels = {s: v for _d, s, f, v, _e in facts if f == "relation"}
    assert rels.get("林砚") == "结盟", facts


def test_disposition_shift_is_captured() -> None:
    """心性转变要落盘为 character/<名>.disposition —— 对应「心性：隐忍→果敢」。"""
    body = "目睹宗门倾轧，林砚终于明白退让换不来活路。"
    facts = _facts(body)
    disp = {s for _d, s, f, _v, _e in facts if f == "disposition"}
    assert "林砚" in disp, facts


def test_negated_design_change_is_not_a_fact() -> None:
    """否定式不算推进（与既有 _negated 守卫同口径）。"""
    body = "林砚没有突破到栖气期。"
    facts = _facts(body)
    assert [f for f in facts if f[2] == "realm"] == [], facts


def test_design_facts_subject_is_entity_not_chapter() -> None:
    """设计维度事实同样遵守实体锚定不变式（subject 绝不是章节号）。"""
    body = "林砚突破到开玄期，与苏晚和解，也终于放下旧怨。"
    for _d, subject, field, _v, _e in _facts(body):
        if field in ("realm", "relation", "disposition"):
            assert not _CH_NUM_RE.match(subject), f"{field} 的 subject 不得是章节号：{subject!r}"


# ============================================================
# 步骤 6b：主角境界推进·跨句兜底（2026-09-24 灵荒工坊 ch043 实证）
# ============================================================
# 步骤 6 要求「角色名与境界名**同句**」，正文把突破写成对白里的独立短句
# （`"突破了。栖气期初期。"`）时两者不在同一句 ⇒ 漏抽 ⇒ 账本承接锚点永不前进
# ⇒ 后续章节按旧境界写 ⇒ 与已发生正文自相矛盾（批末体检判人设崩坏/逻辑漏洞，
# 两项都是不可放宽的硬指标）。下面把「跨句也要结账」钉成红线。


def _seed_realm_project(project) -> None:
    """最小项目：有序境界体系 + 主角 + 承接境界（引灵）。"""
    import json

    (project / "characters").mkdir(parents=True)
    (project / "characters" / "李承安.md").write_text(
        '---\nname: "李承安"\n---\n# 角色档案\n', encoding="utf-8"
    )
    (project / "world.md").write_text(
        "## 修炼境界体系\n"
        "1. **引灵**：引天地灵气入体\n"
        "2. **栖气**：灵气稳定蓄于体内\n"
        "3. **淳真**：灵气提纯转化为淳真之力\n"
        "4. **开玄**：周身玄络贯通\n",
        encoding="utf-8",
    )
    (project / ".state" / "continuity").mkdir(parents=True)
    (project / ".state" / "plan.json").write_text(
        json.dumps({"character_skeleton": [{"name": "李承安", "role": "主角"}]},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    (project / ".state" / "continuity" / "ledger.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "facts": [{
                    "domain": "character", "subject_id": "李承安",
                    "field": "cultivation", "value": "引灵中期",
                    "source_commit_id": "ch1", "evidence": "登记",
                }],
                "knowledge": [], "open_loops": [], "handoffs": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_cross_sentence_realm_declaration_is_captured(tmp_path) -> None:
    """跨句突破宣告（角色名不在同句）也要落盘为 character/<主角>.realm。"""
    from agent.workflows.writing.m5_persist import _extract_chapter_facts

    _seed_realm_project(tmp_path)
    body = (
        "李承安躺到草席上，运转功法。\n轰——\n瓶颈冲破。\n"
        '李承安睁开眼睛。"突破了。栖气期初期。"\n'
    )
    ctx = {"characters_info": "- **李承安**：主角"}
    raw, _must, _cons = _extract_chapter_facts(43, body, ctx, tmp_path)
    realms = {s: v for _d, s, f, v, _e in raw if f == "realm"}
    assert realms.get("李承安"), raw
    assert "栖气" in realms["李承安"], raw


def test_realm_derivation_requires_project_dir(tmp_path) -> None:
    """不给 project_dir（无世界体系/账本可读）时不启用该步——保持纯函数旧行为。"""
    from agent.workflows.writing.m5_persist import _extract_chapter_facts

    _seed_realm_project(tmp_path)
    body = '李承安睁开眼睛。"突破了。栖气期初期。"'
    ctx = {"characters_info": "- **李承安**：主角"}
    raw, _must, _cons = _extract_chapter_facts(43, body, ctx)
    assert [f for f in raw if f[2] == "realm"] == [], raw
