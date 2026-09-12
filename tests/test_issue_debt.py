"""问题债务登记簿（issue_debt）+ presence_conflict 门禁规则测试（2026-09-12）"""

from __future__ import annotations

import pytest

from agent.core.story.issue_debt import (
    KIND_GATE_SKIPPED,
    KIND_PRESENCE_BAN,
    KIND_WATCH,
    IssueDebtError,
    IssueDebtStore,
    render_constraints,
)


def test_add_and_render(tmp_path):
    store = IssueDebtStore(tmp_path)
    store.add(KIND_PRESENCE_BAN, "角色X已死亡，禁止出场", subject="角色X", registered_ch=10)
    store.add(KIND_WATCH, "某设定待定稿统一", registered_ch=12)
    store.save()
    text = render_constraints(tmp_path, 15)
    assert "硬性禁令" in text and "角色X" in text
    assert "已挂账5章" in text
    assert "注意规避" in text and "待定稿统一" in text


def test_persistence_and_resolve(tmp_path):
    store = IssueDebtStore(tmp_path)
    store.add(KIND_PRESENCE_BAN, "禁出场", subject="甲", registered_ch=3)
    debt_id = store.debts[0].id
    store.save()

    store2 = IssueDebtStore(tmp_path).load()
    assert len(store2.open_items()) == 1
    assert store2.resolve(debt_id, "已重写")
    store2.save()

    store3 = IssueDebtStore(tmp_path).load()
    assert store3.open_items() == []
    assert store3.debts[0].status == "resolved"


def test_auto_id_increment(tmp_path):
    store = IssueDebtStore(tmp_path)
    a = store.add(KIND_WATCH, "问题一")
    b = store.add(KIND_WATCH, "问题二")
    assert b.id != a.id and a.id.startswith("DEBT-")


def test_invalid_kind_rejected(tmp_path):
    store = IssueDebtStore(tmp_path)
    with pytest.raises(IssueDebtError):
        store.add("bogus_kind", "约束")


def test_presence_ban_requires_subject(tmp_path):
    store = IssueDebtStore(tmp_path)
    with pytest.raises(IssueDebtError):
        store.add(KIND_PRESENCE_BAN, "禁出场", subject="")


def test_corrupt_file_degrades_to_empty_constraints(tmp_path):
    (tmp_path / ".state").mkdir()
    (tmp_path / ".state" / "issue_debts.json").write_text("{broken", encoding="utf-8")
    assert render_constraints(tmp_path, 5) == ""


def test_presence_conflict_rule_blocks_banned_subject(tmp_path):
    """禁出场主体出现 → 一致性门禁 BLOCK；销账后放行。"""
    from agent.core.quality.consistency.checker import (
        CheckTrigger,
        ConsistencyChecker,
    )

    store = IssueDebtStore(tmp_path)
    store.add(KIND_PRESENCE_BAN, "已前往北境，禁止出场", subject="柳依依", registered_ch=10)
    store.save()

    checker = ConsistencyChecker(tmp_path)
    rep = checker.check(
        CheckTrigger.POST_WRITE, {"chapter_text": "山道上，柳依依远远走来。"}
    )
    assert not rep.passed
    assert any(c.rule_id == "presence_conflict" for c in rep.conflicts)

    # 正文中无被禁主体 → 不触发
    rep2 = checker.check(CheckTrigger.POST_WRITE, {"chapter_text": "山道上空无一人。"})
    assert rep2.passed

    # 销账后 → 放行
    store.resolve(store.debts[0].id, "剧情需要")
    store.save()
    rep3 = checker.check(
        CheckTrigger.POST_WRITE, {"chapter_text": "山道上，柳依依远远走来。"}
    )
    assert rep3.passed


def test_gate_skipped_debt_rendered_as_reminder(tmp_path):
    store = IssueDebtStore(tmp_path)
    store.add(KIND_GATE_SKIPPED, "第7章写时门禁故障放行：质检解析失败", registered_ch=7)
    store.save()
    text = render_constraints(tmp_path, 7)
    assert "gate_skipped" not in text  # kind 不直接出现在文案
    assert "注意规避" in text
