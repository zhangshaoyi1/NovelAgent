"""批间复规划单测（长线一致性设计稿第一期·B）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.agents.planner import Arc, MasterPlan, ReplanOutput
from agent.workflows.pipeline.batch_replan import (
    build_batch_summary,
    count_chapters,
    maybe_replan,
)


def _write_plan(tmp_path: Path, episode_tree: list[Arc]) -> None:
    plan = MasterPlan(brief="测试", episode_tree=episode_tree, total_chapters=100)
    (tmp_path / ".state").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".state" / "plan.json").write_text(
        json.dumps(plan.model_dump(), ensure_ascii=False), encoding="utf-8"
    )


def _fake_decide_factory(rationale: str = "支线B拖沓，下一批转主线"):
    def decide(messages):
        # 校验摘要确实进入 prompt
        assert "【本批进展摘要" in messages[1]["content"]
        return ReplanOutput(
            arcs=[
                Arc(id="x1", name="新弧一", chapter_start=11, chapter_end=25, goal="推进主线"),
                Arc(id="x2", name="新弧二", chapter_start=26, chapter_end=50, goal="收束传承线"),
            ],
            batch_focus="主角与师门决裂",
            batch_subline="main",
            rationale=rationale,
        ).model_dump()

    return decide


def test_count_chapters(tmp_path):
    assert count_chapters(tmp_path) == 0
    ch = tmp_path / "chapters"
    ch.mkdir()
    for i in range(1, 4):
        (ch / f"ch{i:03d}.md").write_text("x", encoding="utf-8")
    assert count_chapters(tmp_path) == 3


def test_replan_batch_merges_arcs_and_writes_directive(tmp_path):
    from agent.agents.planner import PlannerAgent

    _write_plan(
        tmp_path,
        [
            Arc(id="a1", name="已完结弧", chapter_start=1, chapter_end=10, goal="开局"),
            Arc(id="a2", name="过期弧", chapter_start=11, chapter_end=40, goal="旧规划"),
        ],
    )
    planner = PlannerAgent(tmp_path)
    plan = planner.replan_batch(10, "测试摘要", decide=_fake_decide_factory())

    # 已完结弧保留，剩余部分被重排替换；起始章钳制 >= 当前进度+1
    assert [a.name for a in plan.episode_tree] == ["已完结弧", "新弧一", "新弧二"]
    assert plan.episode_tree[1].chapter_start == 11
    assert "[复规划@10]" in plan.notes
    # plan.json 落盘
    assert planner.load_plan().episode_tree[2].name == "新弧二"
    # 下一批裁决落盘
    directive = json.loads((tmp_path / ".state" / "batch_directive.json").read_text(encoding="utf-8"))
    assert directive["chapter"] == 10
    assert directive["focus"] == "主角与师门决裂"
    assert directive["subline"] == "main"
    assert directive["arcs_replanned"] == 2


def test_replan_batch_requires_existing_plan(tmp_path):
    from agent.agents.planner import PlannerAgent

    with pytest.raises(Exception):
        PlannerAgent(tmp_path).replan_batch(10, "x", decide=_fake_decide_factory())


def test_build_batch_summary_sources(tmp_path):
    # 实体名册休眠 + 叙事线进入摘要；无教训时给出通过占位
    from agent.core.story.entity_ledger import EntityLedgerStore

    st = EntityLedgerStore(tmp_path).load()
    st.ensure("老怪", ch=1)
    st.add_obligation("老怪", "传承未揭示", ch=1)
    st.add_thread("传承线", bound_entity="老怪")
    st.save()

    summary = build_batch_summary(tmp_path)
    assert "上一轮体检：通过" in summary
    assert "老怪" in summary and "传承线" in summary


def test_maybe_replan_triggers_on_continuation(tmp_path):
    _write_plan(tmp_path, [Arc(id="a1", name="弧", chapter_start=1, chapter_end=100, goal="g")])
    (tmp_path / "chapters").mkdir()
    for i in range(1, 4):
        (tmp_path / "chapters" / f"ch{i:03d}.md").write_text("x", encoding="utf-8")

    called = {}

    def decide(messages):
        called["ok"] = True
        return ReplanOutput(arcs=[], batch_focus="f", rationale="r").model_dump()

    from rich.console import Console

    assert maybe_replan(tmp_path, console=Console(), decide=decide) is True
    assert called.get("ok") is True


def test_maybe_replan_skips_first_batch(tmp_path):
    # 无 plan.json / 零进度 → 不触发，不调 LLM
    from rich.console import Console

    def decide(messages):  # pragma: no cover - 不应被调用
        raise AssertionError("首批不应触发复规划")

    assert maybe_replan(tmp_path, console=Console(), decide=decide) is False


def test_maybe_replan_degrades_on_failure(tmp_path, caplog):
    # 复规划失败 → 显性降级返回 False，不抛出（不阻断写作）
    _write_plan(tmp_path, [Arc(id="a1", name="弧", chapter_start=1, chapter_end=100, goal="g")])
    (tmp_path / "chapters").mkdir()
    (tmp_path / "chapters" / "ch001.md").write_text("x", encoding="utf-8")

    def decide(messages):
        raise RuntimeError("LLM 爆炸")

    import logging

    from rich.console import Console

    with caplog.at_level(logging.WARNING):
        assert maybe_replan(tmp_path, console=Console(), decide=decide) is False
    assert any("autowrite.batch_replan" in (r.getMessage() or "") for r in caplog.records)
