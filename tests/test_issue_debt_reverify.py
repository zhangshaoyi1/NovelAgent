"""问题债务复查销账红线（2026-09-15）

对应登记单 ``项目文档/优化/20260915_回退熔断账实不符与降级当通过.md`` §三.8 的
传导污染链：``relation_conflict`` 误报 → ``issue_debts`` → 每章原样注入 writer
+ 成为批间复规划的写作焦点。

规则被修好（2026-09-15 10:39 主体锚定）之后，8 条误报**仍为 open**，
规划者据此把"销账 17/18 章林凡/沈长风已故矛盾"写进 ``batch_directive.focus``
—— 一个不存在的矛盾被当成待办，占着写手与规划者的注意力。

本文件钉住的核心纪律：
**「复查后不再命中」与「没能复查」必须分开**——把后者当前者，就会静默销掉
真实债务；把前者当后者，债务永远只进不出。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.core.story.issue_debt import (
    KIND_PRESENCE_BAN,
    KIND_WATCH,
    IssueDebtStore,
    render_constraints,
    reverify,
)

GRAPH = """---
current_chapter: "ch000（初始）"
---

## 节点

| ID | 角色 | 首次登场 |
|---|---|---|
| A | 林凡 | ch001 |
| B | 沈长风 | ch001 |

## 边（关系）

| 起 | 止 | 关系 | 强度 | 起始 | 备注 |
|---|---|---|---|---|---|
| A | B | 合作 | 强 | S01 | 同门 |
| B | A | 合作 | 强 | S01 | 同门 |
"""

#: 误报形态：断言「已故」的主体是**未登记配角**周德顺，附近有名有姓的才是主角。
#: 修复前该断言被栽赃给林凡（`_assertion_subject` 取最近登记角色）。
FALSE_POSITIVE_CH = "林凡在城中巡视。周德顺已经死了，尸骨埋在乱坟岗。\n"

#: 真命中形态：断言主体就是已登记角色本身，且紧邻（间距 0）。
TRUE_POSITIVE_CH = "林凡已经死了。\n"


def _mk_project(tmp_path: Path) -> Path:
    (tmp_path / "characters").mkdir(parents=True, exist_ok=True)
    (tmp_path / "characters" / "林凡.md").write_text(
        "---\nname: 林凡\n---\n\n## 状态\n在世\n", encoding="utf-8"
    )
    (tmp_path / "characters" / "沈长风.md").write_text(
        "---\nname: 沈长风\n---\n\n## 状态\n在世\n", encoding="utf-8"
    )
    (tmp_path / "relations").mkdir(parents=True, exist_ok=True)
    (tmp_path / "relations" / "graph.md").write_text(GRAPH, encoding="utf-8")
    (tmp_path / "chapters").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".state").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _add(
    tmp_path: Path,
    kind: str,
    ch: int,
    *,
    rule_id: str,
    desc: str = "第X章一致性警告",
    subject: str = "",
) -> str:
    store = IssueDebtStore(tmp_path).load()
    debt = store.add(
        kind, constraint=desc, registered_ch=ch, rule_id=rule_id, subject=subject
    )
    store.save()
    return debt.id


def test_reverify_resolves_debt_whose_rule_no_longer_fires(tmp_path: Path) -> None:
    """规则修好 ⇒ 复查不再命中 ⇒ 自动销账（本案：主体锚定后不再栽赃主角）。"""
    proj = _mk_project(tmp_path)
    (proj / "chapters" / "ch001.md").write_text(FALSE_POSITIVE_CH, encoding="utf-8")
    debt_id = _add(proj, KIND_WATCH, 1, rule_id="relation_conflict")

    rep = reverify(proj)

    assert [i for i, _ in rep.resolved] == [debt_id]
    assert "已不再命中" in rep.resolved[0][1]
    store = IssueDebtStore(proj).load()
    assert store.open_items() == [], "销账后不得再出现在未销账列表（否则每章照样注入）"
    assert render_constraints(proj, 5) == "", "销账后写时约束不得再注入该条"


def test_reverify_keeps_debt_whose_rule_still_fires(tmp_path: Path) -> None:
    """规则仍命中 ⇒ 债务必须保留（不得为了让账本好看而销账）。"""
    proj = _mk_project(tmp_path)
    (proj / "chapters" / "ch001.md").write_text(TRUE_POSITIVE_CH, encoding="utf-8")
    debt_id = _add(proj, KIND_WATCH, 1, rule_id="relation_conflict")

    rep = reverify(proj)

    assert rep.resolved == []
    assert [i for i, _ in rep.skipped] == [debt_id]
    assert "仍命中" in rep.skipped[0][1]
    assert [d.id for d in IssueDebtStore(proj).load().open_items()] == [debt_id]
    assert "问题债务" in render_constraints(proj, 5), "仍有效的债务必须继续注入 writer"


def test_missing_chapter_is_not_treated_as_no_hit(tmp_path: Path) -> None:
    """**没能复查 ≠ 不再命中**：原文缺失（如已回滚归档）⇒ 跳过，不得销账。"""
    proj = _mk_project(tmp_path)
    debt_id = _add(proj, KIND_WATCH, 99, rule_id="relation_conflict")  # ch099 不存在

    rep = reverify(proj)

    assert rep.resolved == [], "复查不成功时绝不可当「不再命中」而销账"
    assert [i for i, _ in rep.skipped] == [debt_id]
    assert "无法复查" in rep.skipped[0][1]
    assert [d.id for d in IssueDebtStore(proj).load().open_items()] == [debt_id]


def test_unknown_rule_is_not_treated_as_no_hit(tmp_path: Path) -> None:
    """规则 id 已不存在（改名/删除）⇒ 同样是"无法复查"，不得销账。"""
    proj = _mk_project(tmp_path)
    (proj / "chapters" / "ch001.md").write_text(FALSE_POSITIVE_CH, encoding="utf-8")
    debt_id = _add(proj, KIND_WATCH, 1, rule_id="no_such_rule")

    rep = reverify(proj)

    assert rep.resolved == []
    assert "无法复查" in rep.skipped[0][1]


def test_legacy_debt_without_rule_id_is_skipped(tmp_path: Path) -> None:
    """历史条目（rule_id 为空）来源不可判定 ⇒ 不猜、不动。"""
    proj = _mk_project(tmp_path)
    (proj / "chapters" / "ch001.md").write_text(FALSE_POSITIVE_CH, encoding="utf-8")
    debt_id = _add(proj, KIND_WATCH, 1, rule_id="")

    rep = reverify(proj)

    assert rep.resolved == []
    assert "无 rule_id" in rep.skipped[0][1]
    assert [d.id for d in IssueDebtStore(proj).load().open_items()] == [debt_id]


def test_presence_ban_is_never_auto_resolved(tmp_path: Path) -> None:
    """禁出场是人工作出的禁令：规则修不修都不该被自动解除。"""
    proj = _mk_project(tmp_path)
    (proj / "chapters" / "ch001.md").write_text(FALSE_POSITIVE_CH, encoding="utf-8")
    ban_id = _add(
        proj,
        KIND_PRESENCE_BAN,
        1,
        rule_id="relation_conflict",
        desc="周德顺禁出场",
        subject="周德顺",
    )

    rep = reverify(proj)

    assert rep.resolved == []
    assert [d.id for d in IssueDebtStore(proj).load().open_items()] == [ban_id]


def test_rule_id_round_trips_through_file(tmp_path: Path) -> None:
    """rule_id 必须能落盘再读回——否则复查机制在跨进程的真实链路上哑火。"""
    proj = _mk_project(tmp_path)
    _add(proj, KIND_WATCH, 7, rule_id="relation_conflict")
    debt = IssueDebtStore(proj).load().open_items()[0]
    assert debt.rule_id == "relation_conflict"
    assert debt.registered_ch == 7


@pytest.mark.parametrize("kind", [KIND_WATCH])
def test_reverify_is_idempotent(tmp_path: Path, kind: str) -> None:
    """已销账的条目不会被重复销账（第二次复查 resolved 为空）。"""
    proj = _mk_project(tmp_path)
    (proj / "chapters" / "ch001.md").write_text(FALSE_POSITIVE_CH, encoding="utf-8")
    _add(proj, kind, 1, rule_id="relation_conflict")

    assert reverify(proj).resolved
    again = reverify(proj)
    assert again.resolved == []
    assert again.skipped == []


# ============================================================
# 存量迁移：从约束文本回填 rule_id
# ============================================================
def test_backfill_reads_machine_generated_tag(tmp_path: Path) -> None:
    """老条目把 rule_id 写在「一致性警告（<id>）：」里 —— 只认这个固定格式。"""
    from agent.core.story.issue_debt import backfill_rule_ids

    proj = _mk_project(tmp_path)
    _add(
        proj,
        KIND_WATCH,
        1,
        rule_id="",
        desc="第1章一致性警告（relation_conflict）：关系网显示「林凡」…已故",
    )

    assert backfill_rule_ids(proj) == 1
    debt = IssueDebtStore(proj).load().open_items()[0]
    assert debt.rule_id == "relation_conflict"
    assert backfill_rule_ids(proj) == 0, "已有 rule_id ⇒ 不重复回填"


def test_backfill_refuses_unknown_rule_name(tmp_path: Path) -> None:
    """标签里的规则名已不存在 ⇒ 不回填（宁可留空，不要指向幽灵规则）。"""
    from agent.core.story.issue_debt import backfill_rule_ids

    proj = _mk_project(tmp_path)
    _add(proj, KIND_WATCH, 1, rule_id="", desc="第1章一致性警告（ghost_rule）：…")

    assert backfill_rule_ids(proj) == 0
    assert IssueDebtStore(proj).load().open_items()[0].rule_id == ""


def test_backfill_refuses_free_text(tmp_path: Path) -> None:
    """人工写的自由文本（无固定标签）⇒ 不回填，不猜。"""
    from agent.core.story.issue_debt import backfill_rule_ids

    proj = _mk_project(tmp_path)
    _add(proj, KIND_WATCH, 1, rule_id="", desc="感觉林凡的状态写得有点怪")

    assert backfill_rule_ids(proj) == 0
    assert IssueDebtStore(proj).load().open_items()[0].rule_id == ""


def test_backfill_then_reverify_end_to_end(tmp_path: Path) -> None:
    """存量迁移的真实链路：老误报债务 → 回填 → 复查 → 自动销账。"""
    from agent.core.story.issue_debt import backfill_rule_ids

    proj = _mk_project(tmp_path)
    (proj / "chapters" / "ch001.md").write_text(FALSE_POSITIVE_CH, encoding="utf-8")
    debt_id = _add(
        proj,
        KIND_WATCH,
        1,
        rule_id="",
        desc="第1章一致性警告（relation_conflict）：关系网显示「林凡」存在互动型关系…",
    )

    assert backfill_rule_ids(proj) == 1
    rep = reverify(proj)
    assert [i for i, _ in rep.resolved] == [debt_id]


# ============================================================
# 主体比对：判据必须落到"同一条冲突"，而不是"同一条规则"
# ============================================================
_SUBJECT_CONSTRAINT = (
    "第1章一致性警告（relation_conflict）：关系网(graph.md)显示「林凡」"
    "存在互动型关系（合作，起于 S01），但本章称其已故，关系网一致性存疑。"
)


def test_rule_hits_other_subject_resolves_debt(tmp_path: Path) -> None:
    """规则仍命中，但命中的**换人了** ⇒ 本条所指冲突已消失，销账。

    实测原型：ch18 的主体锚定修复后，死亡断言归因从「林凡」变成「丹王衍」，
    规则照旧返回 1 条命中，于是 3 条「林凡/沈长风」债务被误判为"继续有效"。
    """
    proj = _mk_project(tmp_path)
    # 本章的死亡断言主体是「沈长风」，而债务指的是「林凡」——规则仍命中，人换了
    (proj / "chapters" / "ch001.md").write_text("沈长风已经死了。\n", encoding="utf-8")
    debt_id = _add(
        proj, KIND_WATCH, 1, rule_id="relation_conflict", desc=_SUBJECT_CONSTRAINT
    )

    rep = reverify(proj)

    assert [i for i, _ in rep.resolved] == [debt_id], "主体已不命中 ⇒ 不得继续挂着"
    assert "「林凡」已不再命中" in rep.resolved[0][1]


def test_rule_hits_same_subject_keeps_debt(tmp_path: Path) -> None:
    """命中的正是本条主体 ⇒ 债务必须保留。"""
    proj = _mk_project(tmp_path)
    (proj / "chapters" / "ch001.md").write_text(TRUE_POSITIVE_CH, encoding="utf-8")
    debt_id = _add(
        proj,
        KIND_WATCH,
        1,
        rule_id="relation_conflict",
        desc=_SUBJECT_CONSTRAINT,
    )

    rep = reverify(proj)

    assert rep.resolved == []
    assert "仍命中" in rep.skipped[0][1]
    assert [d.id for d in IssueDebtStore(proj).load().open_items()] == [debt_id]


def test_unlocatable_subject_is_conservative(tmp_path: Path) -> None:
    """约束里定位不到主体（无「」）⇒ 保守按"仍有效"处理，不销账。"""
    proj = _mk_project(tmp_path)
    (proj / "chapters" / "ch001.md").write_text(TRUE_POSITIVE_CH, encoding="utf-8")
    debt_id = _add(
        proj, KIND_WATCH, 1, rule_id="relation_conflict", desc="关系网一致性存疑"
    )

    rep = reverify(proj)

    assert rep.resolved == [], "定位不到主体时不得擅自销账（宁可多留一条）"
    assert [d.id for d in IssueDebtStore(proj).load().open_items()] == [debt_id]
