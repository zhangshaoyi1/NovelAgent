"""设定台账（SettingCanon）单元测试。

对应 2026-09-12 五灵破归档反复回退的 P0-1 修复：设定「只进不出」→ 现在出得去。
"""

from __future__ import annotations

import pytest

from agent.core.story.setting_canon import (
    WORLD_CANON_BEGIN,
    SettingCanon,
    SettingEntry,
    extract_definitions,
)


def test_extract_definition_basic():
    body = "他检查了半天，终于确认阵盘是调频共振吸收的装置。"
    items = extract_definitions(183, body, known_entities=["阵盘"])
    assert items, "应抽到定义性约束"
    e = items[0]
    assert e.subject == "阵盘"
    assert "调频共振" in e.value
    assert e.chapter == 183


def test_extract_attribute_slot():
    body = "林凡翻看旧档：镇魔符的水属性是净化，不是封固。"
    items = extract_definitions(184, body, known_entities=["镇魔符"])
    hit = [i for i in items if i.subject == "镇魔符" and i.attribute == "水属性"]
    assert hit, f"应抽到属性槽定义，实际：{[i.label() for i in items]}"
    assert "净化" in hit[0].value


def test_noise_pronoun_is_filtered():
    body = "他是个好人，这没有任何意义。"
    assert extract_definitions(1, body, known_entities=[]) == []


def test_emerging_term_captured_by_quotes():
    """不在已知实体表的新设定，靠引号术语捕获——这正是要沉淀的对象。"""
    body = "石壁上刻着一行字，这东西叫「引灵枢」，是沟通纹的核心。"
    items = extract_definitions(190, body, known_entities=[])
    assert any(i.subject == "引灵枢" for i in items), [
        (i.subject, i.attribute, i.value) for i in items
    ]


def test_merge_detects_conflict():
    canon = SettingCanon(project_dir=".")
    first = [SettingEntry("阵盘", "", "调频共振吸收", 183)]
    assert canon.merge(first) == []
    # 隔两章模型把同一设定重新发明了一遍
    second = [SettingEntry("阵盘", "", "封印压制其节律", 185)]
    conflicts = canon.merge(second)
    assert len(conflicts) == 1
    assert conflicts[0].old_value == "调频共振吸收"
    assert conflicts[0].new_value == "封印压制其节律"
    # 冲突时保留较早的确立值，不被后文改写
    assert canon.entries["阵盘|"].value == "调频共振吸收"


def test_merge_same_value_no_conflict():
    canon = SettingCanon(project_dir=".")
    canon.merge([SettingEntry("镇魔符", "水属性", "净化", 184)])
    assert canon.merge([SettingEntry("镇魔符", "水属性", "净化", 188)]) == []


def test_save_and_load_roundtrip(tmp_path):
    canon = SettingCanon(project_dir=tmp_path)
    canon.merge([SettingEntry("阵盘", "", "调频共振吸收", 183)])
    canon.save()
    assert (tmp_path / ".state/continuity/setting_canon.json").exists()
    loaded = SettingCanon.load(tmp_path)
    assert "阵盘|" in loaded.entries
    assert loaded.entries["阵盘|"].value == "调频共振吸收"


def test_sync_to_world_md_writes_managed_block(tmp_path):
    world = tmp_path / "world.md"
    world.write_text("# 世界观设定\n\n## 境界体系\n\n炼气、筑基、金丹。\n", encoding="utf-8")

    canon = SettingCanon(project_dir=tmp_path)
    canon.merge([SettingEntry("阵盘", "", "调频共振吸收", 183)])
    assert canon.sync_to_world_md() is True

    text = world.read_text(encoding="utf-8")
    assert WORLD_CANON_BEGIN in text
    assert "阵盘 = 调频共振吸收" in text
    assert "## 境界体系" in text, "原有内容不能被覆盖"

    # 幂等：内容没变时不再写盘
    assert canon.sync_to_world_md() is False


def test_sync_to_world_md_replaces_block_on_update(tmp_path):
    world = tmp_path / "world.md"
    world.write_text("# 世界观设定\n", encoding="utf-8")
    canon = SettingCanon(project_dir=tmp_path)
    canon.merge([SettingEntry("阵盘", "", "调频共振吸收", 183)])
    canon.sync_to_world_md()
    canon.merge([SettingEntry("镇魔符", "水属性", "净化", 184)])
    canon.sync_to_world_md()

    text = world.read_text(encoding="utf-8")
    assert text.count(WORLD_CANON_BEGIN) == 1, "托管区块只能有一份"
    assert "镇魔符·水属性 = 净化" in text


def test_render_for_prompt_includes_hard_constraint():
    canon = SettingCanon(project_dir=".")
    canon.merge([SettingEntry("阵盘", "", "调频共振吸收", 183)])
    txt = canon.render_for_prompt()
    assert "禁止改写或重新发明" in txt
    assert "阵盘" in txt


def test_render_conflicts_empty_when_clean():
    assert SettingCanon(project_dir=".").render_conflicts() == ""


@pytest.mark.parametrize("body", ["", "---\ntitle: x\n---\n"])
def test_extract_empty_body(body):
    assert extract_definitions(1, body) == []


# ---------------------------------------------------------------------------
# 真实语料回放（2026-09-12，五灵破归档 ch176-185 原文）
# 收紧前的实况：受「主体须是已知角色/引号术语」门槛所限，全 10 章只抽出 2 条，
# 且两条都是比喻噪音（「林凡·声音很轻，轻得像 = 在陈述一个事实」）。
# 下面这些句子是**书中原句**，用来钉住「该死的设定被抽到、噪音被挡掉」。
# ---------------------------------------------------------------------------


def test_real_corpus_captures_emerging_setting():
    """正文明确定义的涌现设定必须被抽到（旧门槛把它们全挡在门外）。"""
    body = "镇灵符是压制灵力波动的低阶符箓，贴在地面上可以稳定方圆三丈内的灵力频率。"
    items = extract_definitions(183, body)
    pairs = {e.label(): e.value for e in items}
    assert any(e.subject == "镇灵符" for e in items), f"涌现设定未抽出：{pairs}"
    assert any("压制灵力波动" in v for v in pairs.values()), pairs


def test_real_corpus_attribute_slot():
    """「X 的 A 是 B」属性槽：阵盘的制式是上古归档阵的残页。"""
    body = "阵盘的制式是上古归档阵的残页，这种东西散修手里没有，宗门也拿不到。"
    items = extract_definitions(184, body)
    hit = [e for e in items if e.subject == "阵盘" and e.attribute == "制式"]
    assert hit, [e.label() for e in items]
    assert "上古归档阵" in hit[0].value


def test_real_corpus_no_metaphor_leak():
    """比喻/叙述不得混进台账（旧实况：「声音很轻，轻得像…」被当成定义）。"""
    body = "林凡的声音很轻，轻得像是在陈述一个事实。"
    assert extract_definitions(176, body) == [], "比喻句不得沉淀为设定"


@pytest.mark.parametrize(
    "noise",
    [
        "然后是脚步声，由远及近，越来越急。",          # 主体切出「然后」
        "三号炉产量高，但它的灵气转化率只有百分之六十。",  # 主体切出「但转化率」
        "两人看到是林凡，脸上的表情各不相同。",          # 主体切出「两人看到」
        "名单上是三十七人，走了六个。",                  # 主体切出「名单上」
        "月光下，那个人脸色惨白，额头上满是冷汗。",      # 主体切出「额头上满」
        "有气泡从裂谷壁的裂缝里往外冒，颜色是暗红的。",    # 取值是形容词性描写
    ],
)
def test_real_corpus_noise_filtered(noise: str) -> None:
    assert extract_definitions(180, noise) == [], f"噪音未被挡下：{noise}"


def test_term_like_filters_dialogue_fragments():
    """ASCII 对白引号配对产生的片段不得当成术语。"""
    from agent.core.story.setting_canon import is_term_like

    assert is_term_like("阵盘")
    assert is_term_like("镇灵符")
    for junk in ["能恢复。", "我知道。", "撤不撤。", "加两成？", "没有醒。"]:
        assert not is_term_like(junk), f"对白片段被判为术语：{junk}"
