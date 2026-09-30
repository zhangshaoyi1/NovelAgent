"""T2 真相断言账本（长线一致性二期，20260930 登记）单测。

注意 API 口径：``TruthLedgerStore.load()`` 返回账本对象（与 continuity 一期同风格）；
查询/渲染走 store 实例方法（``.active()`` / ``.render_for_brief()``）。
"""

from agent.core.story.truth_ledger import (
    TruthAssertion,
    TruthLedgerStore,
    propose_truth,
)


def _store(tmp_path) -> TruthLedgerStore:
    store = TruthLedgerStore(tmp_path)
    store.load()
    return store


def test_propose_creates_and_persists(tmp_path):
    v = propose_truth(tmp_path, claim="林凡之父死于血引阵枢自验", kind="death", chapter=128,
                      reason="ch128 定稿口径")
    assert v.applied
    store = _store(tmp_path)
    assert len(store.active()) == 1
    assert store.active()[0].claim.startswith("林凡之父")


def test_supersede_keeps_history(tmp_path):
    propose_truth(tmp_path, claim="父亲死于意外坠崖", kind="death", chapter=10)
    v = propose_truth(tmp_path, claim="父亲死于血引阵枢自验被抽空灵力", kind="death",
                      chapter=128, supersede_ids=["T001"], reason="ch128 收口")
    assert v.applied
    store = _store(tmp_path)
    active = store.active()
    assert len(active) == 1 and "血引阵枢" in active[0].claim
    old = store.ledger.assertions[0]
    assert old.status == "superseded" and old.superseded_by == "T002"
    assert old.superseded_reason == "ch128 收口"


def test_supersede_unknown_id_rejected(tmp_path):
    _store(tmp_path)
    v = propose_truth(tmp_path, claim="X", kind="worldview", supersede_ids=["T999"])
    assert not v.applied and "不存在" in v.reason


def test_supersede_twice_rejected(tmp_path):
    propose_truth(tmp_path, claim="A", kind="worldview")
    propose_truth(tmp_path, claim="B", kind="worldview", supersede_ids=["T001"])
    v = propose_truth(tmp_path, claim="C", kind="worldview", supersede_ids=["T001"])
    assert not v.applied and "不可再推翻" in v.reason


def test_model_rejects_superseded_without_ref():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TruthAssertion(assertion_id="T001", kind="death", claim="x", status="superseded")


def test_writer_path_api_surface(tmp_path):
    """红线：写手/质检侧只允许读 API；变更只能走 propose_truth（裁决通道）。"""
    store = TruthLedgerStore(tmp_path)
    public = {m for m in dir(store) if not m.startswith("_")}
    assert public <= {"load", "save", "active", "render_for_brief", "project_dir", "file", "ledger"}, (
        f"TruthLedgerStore 出现越权写方法: {public}"
    )


def test_render_for_brief_empty_and_full(tmp_path):
    store = _store(tmp_path)
    assert store.render_for_brief() == ""
    propose_truth(tmp_path, claim="五行吞噬诀由父亲所授", kind="origin", chapter=128)
    store = _store(tmp_path)
    rendered = store.render_for_brief()
    assert "真相断言" in rendered and "五行吞噬诀" in rendered and "裁决" in rendered


def test_design_brief_injects_truth(tmp_path):
    """三端同源：账本非空时写手/评委渲染均含真相断言块。"""
    from agent.core.story.design_brief import build_design_brief

    propose_truth(tmp_path, claim="周德海已停职审查、未死", kind="death", chapter=114)
    brief = build_design_brief(tmp_path, chapter_num=5)
    assert "真相断言" in brief.render_for_writer()
    assert "真相断言" in brief.render_for_judge()
