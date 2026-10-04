"""规划闸门单测（登记单 20261004_规划质量分层守卫·件 2/3）。

覆盖：确定性硬冲突（fact_card 死亡名单 + truth_ledger 死亡断言两条路径）、
enforce_plan_gate 全处置分支（pass / revise 计数 / escalate / override /
disabled / no_llm / unavailable）、PlanStore.mutate 集成（拒写不落盘不留史）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.core.plan_gate import (
    PLAN_GATE_LOG,
    PlanGateRejected,
    enforce_plan_gate,
    hard_conflict_check,
    plan_gate_enabled,
)
from agent.core.plan_store import PlanStore
from agent.core.story.truth_ledger import propose_truth


# ---------------------------------------------------------------- fixtures
@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / ".state").mkdir(parents=True)
    return tmp_path


def _write_fact_dead(project: Path, dead: dict[str, int]) -> None:
    """播种事实卡死亡名单（.state/continuity/fact_cards.json）。"""
    f = project / ".state" / "continuity" / "fact_cards.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"dead": dead}), encoding="utf-8")


class _FakeReview:
    """可编程评审 LLM：依次返回预设 verdict（None = 抛异常模拟不可用）。

    形状对齐 ``chat_utility_structured`` 的消费面：``gateway.chat(req)`` →
    响应对象带 ``.text``（JSON 字符串）。
    """

    def __init__(self, results: list) -> None:
        self.results = list(results)
        self.calls = 0

    def chat(self, request: object, **kw: object) -> object:
        self.calls += 1
        step = self.results.pop(0) if self.results else None
        if step is None:
            raise RuntimeError("评审 LLM 不可用")
        return type("Resp", (), {"text": step.model_dump_json()})()


def _verdict(verdict: str, feedback: str = "") -> object:
    from agent.core.plan_gate import PlanGateOutput

    return PlanGateOutput(verdict=verdict, feedback=feedback)


# ---------------------------------------------------------------- 硬冲突
def test_hard_conflict_hits_fact_dead_revive(project: Path) -> None:
    _write_fact_dead(project, {"林某": 3})
    plan = {"route": {"nodes": [{"main_branch": {"result": "林某复活后执掌宗门"}}]}}
    conflicts = hard_conflict_check(project, plan)
    assert len(conflicts) == 1
    assert "林某" in conflicts[0] and "复活" in conflicts[0]
    assert "main_branch.result" in conflicts[0] and "第3章" in conflicts[0]


def test_hard_conflict_no_revive_word_passes(project: Path) -> None:
    _write_fact_dead(project, {"林某": 3})
    plan = {"route": {"nodes": [{"main_branch": {"result": "林某之女继承遗志"}}]}}
    assert hard_conflict_check(project, plan) == []


def test_hard_conflict_no_dead_names_passes(project: Path) -> None:
    _write_fact_dead(project, {})
    plan = {"route": {"nodes": [{"main_branch": {"result": "林某复活"}}]}}
    assert hard_conflict_check(project, plan) == []


def test_hard_conflict_truth_death_assertion_path(project: Path) -> None:
    # 事实卡死亡名单 + 真相账本死亡断言同在：断言 id 出现在冲突证据里
    _write_fact_dead(project, {"周德海": 88})
    v = propose_truth(project, claim="周德海死于灵脉枯竭", kind="death", chapter=88)
    assert v.applied
    plan = {"route": {"nodes": [{"note": "周德海起死回生再战"}]}}
    conflicts = hard_conflict_check(project, plan)
    assert len(conflicts) == 1
    assert "周德海" in conflicts[0] and "T001" in conflicts[0]


# ---------------------------------------------------------------- enforce 分支
def _store(project: Path) -> PlanStore:
    return PlanStore(project)


def _write_plan_file(project: Path, plan: dict) -> None:
    (project / ".state" / "plan.json").write_text(
        json.dumps(plan, ensure_ascii=False), encoding="utf-8"
    )


def test_enforce_pass_allows(project: Path, monkeypatch) -> None:
    monkeypatch.setenv("NOVEL_PLAN_GATE_REVIEW", "1")
    _write_plan_file(project, {"total_chapters": 100})
    enforce_plan_gate(
        _store(project), {"total_chapters": 100}, {"total_chapters": 120},
        review_llm=_FakeReview([_verdict("pass", "ok")]), reason="测试",
    )
    # pass 清空打回计数
    refusals = project / ".state" / "plan_gate_refusals.json"
    assert not refusals.exists() or json.loads(refusals.read_text(encoding="utf-8")) == {}


def test_enforce_revise_twice_then_escalate(project: Path, monkeypatch) -> None:
    monkeypatch.setenv("NOVEL_PLAN_GATE_REVIEW", "1")
    old = {"total_chapters": 100}
    new = {"total_chapters": 120}
    llm = _FakeReview([_verdict("revise", "改弧线")] * 3)
    # 第 1、2 次 revise → 拒写（无检查点）
    for i in (1, 2):
        with pytest.raises(PlanGateRejected):
            enforce_plan_gate(_store(project), old, new, review_llm=llm, reason="r")
        assert not (project / ".state" / "checkpoint.json").exists()
    # 第 3 次 revise → 挂起 + 拒写
    with pytest.raises(PlanGateRejected, match="挂起"):
        enforce_plan_gate(_store(project), old, new, review_llm=llm, reason="r")
    card = json.loads((project / ".state" / "checkpoint.json").read_text(encoding="utf-8"))
    assert card["paused"] and "两次修订仍不过" in card["pause_reason"]


def test_enforce_revise_count_cleared_by_pass(project: Path, monkeypatch) -> None:
    monkeypatch.setenv("NOVEL_PLAN_GATE_REVIEW", "1")
    old = {"total_chapters": 100}
    new = {"total_chapters": 120}
    with pytest.raises(PlanGateRejected):
        enforce_plan_gate(
            _store(project), old, new,
            review_llm=_FakeReview([_verdict("revise", "x")]), reason="r",
        )
    # 同 signature 一次 pass 后计数清零 → 可再打回 2 次
    enforce_plan_gate(
        _store(project), old, new,
        review_llm=_FakeReview([_verdict("pass")]), reason="r",
    )
    with pytest.raises(PlanGateRejected):
        enforce_plan_gate(
            _store(project), old, new,
            review_llm=_FakeReview([_verdict("revise", "y")]), reason="r",
        )
    assert not (project / ".state" / "checkpoint.json").exists()


def test_enforce_override_writes_waiver_no_review(project: Path, monkeypatch) -> None:
    monkeypatch.setenv("NOVEL_PLAN_GATE_REVIEW", "1")
    _write_fact_dead(project, {"林某": 3})  # 即使硬冲突，override 也可豁免
    new = {"route": {"note": "林某复活"}}
    enforce_plan_gate(
        _store(project), {}, new,
        review_llm=_FakeReview([]), reason="作者拍板", override=True,
    )
    waiver = project / ".state" / "plan_gate_waivers.jsonl"
    assert waiver.exists()
    rec = json.loads(waiver.read_text(encoding="utf-8").splitlines()[0])
    assert rec["reason"] == "作者拍板" and "route" in rec["keys_changed"]
    # 豁免路径不产生拒写记录（无 PlanGateRejected 即视为放行）


def test_enforce_disabled_allows(project: Path, monkeypatch) -> None:
    monkeypatch.setenv("NOVEL_PLAN_GATE_REVIEW", "0")
    assert plan_gate_enabled() is False
    _write_fact_dead(project, {"林某": 3})
    # 开关关：连硬冲突都不拦（总闸优先），只留痕
    enforce_plan_gate(
        _store(project), {}, {"route": {"note": "林某复活"}},
        review_llm=None, reason="r",
    )
    log = project / ".state" / "plan_gate.jsonl"
    assert json.loads(log.read_text(encoding="utf-8").splitlines()[0])["source"] == "disabled"


def test_enforce_no_llm_logs_and_allows(project: Path, monkeypatch, caplog) -> None:
    monkeypatch.setenv("NOVEL_PLAN_GATE_REVIEW", "1")
    _write_plan_file(project, {"total_chapters": 100})
    enforce_plan_gate(
        _store(project), {"total_chapters": 100}, {"total_chapters": 120},
        review_llm=None, reason="人工编辑",
    )
    log = project / ".state" / "plan_gate.jsonl"
    assert json.loads(log.read_text(encoding="utf-8").splitlines()[0])["source"] == "no_llm"


def test_enforce_unavailable_suspends(project: Path, monkeypatch) -> None:
    monkeypatch.setenv("NOVEL_PLAN_GATE_REVIEW", "1")
    _write_plan_file(project, {"total_chapters": 100})
    llm = _FakeReview([None, None])  # 两次尝试均异常
    with pytest.raises(PlanGateRejected, match="不可用"):
        enforce_plan_gate(
            _store(project), {"total_chapters": 100}, {"total_chapters": 120},
            review_llm=llm, reason="r",
        )
    assert llm.calls == 2  # 内部重试一次
    card = json.loads((project / ".state" / "checkpoint.json").read_text(encoding="utf-8"))
    assert card["paused"] and "评审不可用" in card["pause_reason"]


# ---------------------------------------------------------------- mutate 集成
def test_mutate_gate_rejected_keeps_plan_and_history(project: Path, monkeypatch) -> None:
    monkeypatch.setenv("NOVEL_PLAN_GATE_REVIEW", "1")
    _write_plan_file(project, {"total_chapters": 100})
    _write_fact_dead(project, {"林某": 3})
    hist = project / ".state" / "plan_history.json"
    hist.write_text("[]", encoding="utf-8")

    with pytest.raises(PlanGateRejected):
        PlanStore(project).mutate(
            lambda p: {**p, "route": {"note": "林某复活"}},
            reason="测试拒写",
            review_llm=None,  # 人工路径；硬冲突仍恒拦（在 no_llm 放行之前）
        )
    # plan.json 内容不变、plan_history 不新增
    assert json.loads((project / ".state" / "plan.json").read_text(encoding="utf-8")) == {
        "total_chapters": 100
    }
    assert json.loads(hist.read_text(encoding="utf-8")) == []


def test_mutate_gate_pass_writes(project: Path, monkeypatch) -> None:
    monkeypatch.setenv("NOVEL_PLAN_GATE_REVIEW", "1")
    _write_plan_file(project, {"total_chapters": 100})
    result = PlanStore(project).mutate(
        lambda p: {**p, "total_chapters": 120},
        reason="测试放行",
        review_llm=_FakeReview([_verdict("pass")]),
    )
    assert result["total_chapters"] == 120
    assert json.loads((project / ".state" / "plan.json").read_text(encoding="utf-8"))[
        "total_chapters"
    ] == 120
