"""plan_reviewer 语义反转 + 重试单测（登记单 20261004·件 1/5）。

覆盖：apply_plan_review unavailable→escalate；review_batch_plan 首次异常
重试一次（mock 第一次抛第二次成功 → 正常 verdict）；escalate_to_checkpoint
薄封装仍写检查点卡 + 状态迁移。
"""

from __future__ import annotations

import json

import pytest

from agent.agents.plan_reviewer import (
    PlanReviewOutput,
    PlanReviewResult,
    apply_plan_review,
    escalate_to_checkpoint,
    review_batch_plan,
)


def _verdict(verdict: str, feedback: str = "") -> PlanReviewOutput:
    return PlanReviewOutput(verdict=verdict, feedback=feedback)


class _FlakyLlm:
    """第一次 chat 抛异常、第二次返回预设 verdict（验证内部重试）。"""

    def __init__(self, ok_verdict: PlanReviewOutput) -> None:
        self.ok = ok_verdict
        self.calls = 0

    def chat(self, request: object, **kw: object) -> object:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("网络抖动")
        return type("Resp", (), {"text": self.ok.model_dump_json()})()


# ---------------------------------------------------------------- apply_plan_review
def test_apply_unavailable_escalates() -> None:
    action, feedback = apply_plan_review(
        PlanReviewResult("unavailable", "", source="degraded"), None
    )
    assert action == "escalate", "规划级评审不可用不得自动放行（登记单 20261004）"
    assert feedback


def test_apply_unavailable_after_revise_escalates() -> None:
    action, _ = apply_plan_review(
        PlanReviewResult("revise", "x"),
        PlanReviewResult("unavailable", "", source="degraded"),
    )
    assert action == "escalate"


def test_apply_pass_proceeds_and_disabled_unchanged() -> None:
    assert apply_plan_review(PlanReviewResult("pass", ""), None)[0] == "proceed"
    assert apply_plan_review(PlanReviewResult("pass", "开关关"), None)[0] == "proceed"


def test_apply_revise_then_pass_proceeds() -> None:
    action, _ = apply_plan_review(
        PlanReviewResult("revise", "x"), PlanReviewResult("pass", "")
    )
    assert action == "proceed"


# ---------------------------------------------------------------- review_batch_plan
def test_review_batch_plan_retries_once(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NOVELAGENT_PLAN_REVIEW", "1")
    plan = type("P", (), {"episode_tree": [], "total_chapters": 100})()
    llm = _FlakyLlm(_verdict("pass", "二次成功"))
    result = review_batch_plan(tmp_path, plan, 3, "摘要", llm=llm)
    assert llm.calls == 2
    assert result.verdict == "pass" and result.source == "llm"


def test_review_batch_plan_unavailable_after_two_attempts(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NOVELAGENT_PLAN_REVIEW", "1")
    plan = type("P", (), {"episode_tree": [], "total_chapters": 100})()

    class _Dead:
        calls = 0

        def chat(self, request: object, **kw: object) -> object:
            self.calls += 1
            raise RuntimeError("LLM 不可用")

    llm = _Dead()
    result = review_batch_plan(tmp_path, plan, 3, "摘要", llm=llm)
    assert llm.calls == 2
    assert result.verdict == "unavailable" and result.source == "degraded"


# ---------------------------------------------------------------- escalate 薄封装
def test_escalate_to_checkpoint_writes_card(tmp_path) -> None:
    from agent.core.engine.state_machine import State, StateMachine

    (tmp_path / ".state").mkdir()
    sm = StateMachine(tmp_path)
    sm.state = State.WRITING
    sm.save()

    escalate_to_checkpoint(tmp_path, "分歧反馈", console=None)
    card = json.loads((tmp_path / ".state" / "checkpoint.json").read_text(encoding="utf-8"))
    assert card["paused"] and "计划审稿人" in card["pause_reason"]
    assert any("计划评审分歧" in r for r in card["risks"])
