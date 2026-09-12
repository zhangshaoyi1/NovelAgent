"""完本收束计划单测（长线一致性设计稿第一期·E，纯离线零 LLM）。"""

from __future__ import annotations

import json
from pathlib import Path

from agent.core.story.closure_plan import (
    CLOSURE_FILE,
    build_closure_plan,
    load_closure_plan,
    render_closure_text,
    save_closure_plan,
)
from tests.conftest import _build_minimal_project


def _seed_fixtures(proj: Path) -> None:
    # foreshadows.md：1 已回收 + 2 未回收
    (proj / "foreshadows.md").write_text(
        "| F-01 | 断剑之谜 | S01/E01/ch003 | S02/E02 | 已回收 | 主角 |\n"
        "| F-02 | 长老身世 | S01/E02/ch010 | 大结局 | 已埋 | 长老 |\n"
        "| F-03 | 魔渊封印 | S02/E01 | 大结局 | 未埋 | 反派 |\n",
        encoding="utf-8",
    )
    from agent.core.story.entity_ledger import EntityLedgerStore

    st = EntityLedgerStore(proj).load()
    st.ensure("老怪", ch=5)
    st.add_obligation("老怪", "传承最深一层未揭示", ch=5)
    t = st.add_thread("青云传承线", bound_entity="老怪", urgency="high")
    st.advance_thread(t.id, 12, "第一道传承")
    st.save()

    from agent.core.story.issue_debt import KIND_WATCH, IssueDebtStore

    ds = IssueDebtStore(proj)
    ds.add(KIND_WATCH, "第10章一致性警告：时间线模糊", registered_ch=10)
    ds.save()


def test_build_closure_plan_collects_all(tmp_path: Path) -> None:
    proj = _build_minimal_project(tmp_path)
    _seed_fixtures(proj)
    plan = build_closure_plan(proj)

    assert {f["fid"] for f in plan["foreshadows"]} == {"F-02", "F-03"}  # 已回收不入清单
    assert plan["threads"][0]["name"] == "青云传承线"
    assert plan["obligations"] == [{"entity": "老怪", "text": "传承最深一层未揭示"}]
    assert plan["issue_debts"][0]["constraint"].startswith("第10章")
    assert plan["total_open"] == 2 + 1 + 1 + 1


def test_save_load_roundtrip(tmp_path: Path) -> None:
    proj = _build_minimal_project(tmp_path)
    plan = build_closure_plan(proj)
    save_closure_plan(proj, plan)
    assert load_closure_plan(proj)["total_open"] == plan["total_open"]
    assert (proj / CLOSURE_FILE).exists()


def test_load_missing_and_corrupt(tmp_path: Path) -> None:
    proj = _build_minimal_project(tmp_path)
    assert load_closure_plan(proj) == {}
    p = proj / CLOSURE_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{broken", encoding="utf-8")
    assert load_closure_plan(proj) == {}  # 损坏 → degrade 空处理


def test_render_closure_text(tmp_path: Path) -> None:
    proj = _build_minimal_project(tmp_path)
    _seed_fixtures(proj)
    plan = build_closure_plan(proj)
    text = render_closure_text(plan)
    assert "完本收束清单" in text and "共 5 项" in text
    assert "F-02" in text and "青云传承线" in text and "传承最深一层" in text
    # 空计划 → 空串
    assert render_closure_text({"total_open": 0}) == ""
    assert render_closure_text({}) == ""


def test_render_corpus_text_escaping(tmp_path: Path) -> None:
    # 内容含管道符等表格残留不影响渲染（来自 parser 的干净字段）
    plan = {"total_open": 1, "foreshadows": [
        {"fid": "F-09", "content": "含|竖线", "state": "已埋", "expected_resolve": "终章"}]}
    text = render_closure_text(plan)
    assert "含|竖线" in text and "F-09" in text


def test_closure_json_shape_stable(tmp_path: Path) -> None:
    proj = _build_minimal_project(tmp_path)
    _seed_fixtures(proj)
    plan = build_closure_plan(proj)
    # json 可序列化（落盘不炸）
    json.dumps(plan, ensure_ascii=False)
