"""T1 事实卡对账（长线一致性二期）单测。

用例直接取自《回归基线_灵荒工坊连续性golden_cases》的代表条目（H48/H39/H19/H04），
作为判据的活文档：改判据先跑这里。
"""

from agent.core.continuity.fact_card import (
    _ReconcileState,
    cn2int,
    extract_fact_card,
    reconcile_book,
    reconcile_text,
)


def test_cn2int_mixed():
    assert cn2int("十二") == 12
    assert cn2int("两") == 2
    assert cn2int("四十九") == 49
    assert cn2int("37") == 37
    assert cn2int("百") == 100
    assert cn2int("廿三") is None  # 不支持的口径显式放弃，不猜


def test_day_mark_conflict_same_chapter():
    # H48: 同章「最后一天」与「还有三天」并存
    text = "掌心的刻痕清晰：一。初九。最后一天。他握紧拳。两天。他摇摇头。三天。他只有三天。"
    state = _ReconcileState()
    issues = reconcile_text(129, text, state)
    rules = {i.rule_id for i in issues}
    assert "countdown_conflict" in rules
    assert any(i.severity == "error" for i in issues if i.rule_id == "countdown_conflict")


def test_day_mark_regression_across_chapters():
    # H23 风格：当日账第49日，「昨天的记录」却是第36日
    state = _ReconcileState()
    reconcile_text(50, "账册最新一行：第四十九日。", state)
    issues = reconcile_text(51, "他翻昨天的记录，上面写着第36日，调谐器嵌于深处。", state)
    hits = [i for i in issues if i.rule_id == "day_ref_conflict" and i.severity == "error"]
    assert hits and "昨天" in hits[0].message


def test_day_mark_small_regression_is_warn():
    state = _ReconcileState()
    reconcile_text(10, "第十日，他记录数据。", state)
    issues = reconcile_text(11, "回看第九日的笔记，他若有所思。", state)
    assert any(i.rule_id == "day_mark_conflict" and i.severity == "warn" for i in issues)


def test_flashback_does_not_trip_day_rule():
    state = _ReconcileState()
    reconcile_text(10, "第十日，进度过半。", state)
    issues = reconcile_text(11, "他回忆起第三日的雨夜，那时他差点丧命。", state)
    assert not [i for i in issues if i.rule_id == "day_mark_conflict" and i.severity == "error"]


def test_death_irreversibility():
    # H39 风格：某章宣告死亡，后章复活说话
    state = _ReconcileState()
    reconcile_text(105, "陈长老走了。周德海病故了，尸身停在偏殿。", state)
    issues = reconcile_text(108, "周德海说道：「此事已了。」众人散去。", state)
    hits = [i for i in issues if i.rule_id == "death_irreversibility" and i.severity == "error"]
    assert hits and "周德海" in hits[0].message


def test_death_in_flashback_ok():
    state = _ReconcileState()
    reconcile_text(30, "老张头病故了。", state)
    issues = reconcile_text(40, "他梦中又见老张头，老张头笑着朝他挥手。", state)
    assert not [i for i in issues if i.rule_id == "death_irreversibility"]


def test_sold_then_used():
    # H04: 卖掉的残灵石又被取出
    state = _ReconcileState()
    reconcile_text(19, "他把八块残灵石全部卖给了执事堂商人，换了三颗培元丹。", state)
    issues = reconcile_text(19, "夜里，他取出残灵石，又从抽屉里翻出一枚备用。", state)
    assert any(i.rule_id == "sold_then_used" and i.severity == "error" for i in issues)


def test_acquire_clears_item_state():
    state = _ReconcileState()
    reconcile_text(1, "他把丹瓶卖给了铺子。", state)
    reconcile_text(2, "他重新买了丹瓶，仔细收好。", state)
    issues = reconcile_text(3, "他拿出丹瓶倒出一粒。", state)
    assert not [i for i in issues if i.rule_id == "sold_then_used"]


def test_reconcile_book_sorted_order():
    chapters = {
        2: "第6日，他继续赶路。",
        1: "第5日，他出发了。",
        3: "第2日，他抵达。",
    }
    issues = reconcile_book(chapters)
    assert any("倒退" in i.message for i in issues)
