"""规划层语义评审红线（登记单 20261001_信任链与叙事上限六项能力·子项 4）。

守卫三条验收标准：
- **V4.1 真阻断权**：first=revise → 打回一次；打回后仍 revise 或任一次
  infeasible → escalate（升级检查点挂起，PlanReviewEscalation 穿透 batch_replan
  的通用 degrade except——M5 评委"恒不阻断"的教训不得重演）。
- **V4.2 评审结论留痕**：每次裁决写 `.state/plan_review.jsonl`（只追加）。
- **V4.3 唯一消费点**：batch_replan 内、plan_managers 确定性审计之前必须调用
  review_batch_plan（红线测试静态断言，防"看起来接了"）。
- 降级纪律：LLM 不可用 / 输出非法 → unavailable → 按 pass 继续（评审缺席
  不阻断复规划）；`NOVELAGENT_PLAN_REVIEW=0` 关闭（关闭也留痕）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.agents.plan_reviewer import (
    PLAN_REVIEW_LOG,
    PlanReviewEscalation,
    PlanReviewResult,
    apply_plan_review,
    escalate_to_checkpoint,
    review_batch_plan,
)


# ---------------------------------------------------------------- 夹具
class _FakeResp:
    def __init__(self, text: str):
        self.text = text


class _FakeLLM:
    def __init__(self, verdict: str | None = "pass", feedback: str = ""):
        self.verdict = verdict
        self.feedback = feedback
        self.calls = 0

    def chat(self, req):
        self.calls += 1
        if self.verdict is None:
            raise RuntimeError("网关不可达")
        return _FakeResp(json.dumps({"verdict": self.verdict, "feedback": self.feedback}))


class _Console:
    def print(self, *args, **kwargs):  # noqa: D102
        pass


class _Arc:
    def __init__(self, id="A2", name="复仇线", start=8, end=12, goal="复仇", subline="S01"):
        self.id = id
        self.name = name
        self.chapter_start = start
        self.chapter_end = end
        self.goal = goal
        self.subline_id = subline


class _Plan:
    episode_tree = [_Arc()]
    total_chapters = 100


# ---------------------------------------------------------------- 纯决策函数（V4.1）
def test_apply_pass_proceeds() -> None:
    action, _ = apply_plan_review(PlanReviewResult("pass", ""), None)
    assert action == "proceed"


def test_apply_unavailable_escalates() -> None:
    # 登记单 20261004 语义反转：规划级评审不可用不再自动放行，改挂起交人工。
    action, feedback = apply_plan_review(
        PlanReviewResult("unavailable", "", source="degraded"), None
    )
    assert action == "escalate", "评审不可用必须挂起，不得按 pass 继续"
    assert isinstance(feedback, str)


def test_apply_revise_then_pass_proceeds() -> None:
    action, _ = apply_plan_review(
        PlanReviewResult("revise", "x"), PlanReviewResult("pass", "")
    )
    assert action == "proceed"


def test_apply_revise_without_second_returns_replan() -> None:
    action, _ = apply_plan_review(PlanReviewResult("revise", "x"), None)
    assert action == "replan"


def test_apply_revise_twice_escalates() -> None:
    action, fb = apply_plan_review(
        PlanReviewResult("revise", "弧线 A3 强度太平"),
        PlanReviewResult("revise", "仍未修"),
    )
    assert action == "escalate"
    assert "仍未修" in fb


def test_apply_infeasible_escalates_immediately() -> None:
    action, _ = apply_plan_review(PlanReviewResult("infeasible", "前提荒谬"), None)
    assert action == "escalate"


# ---------------------------------------------------------------- 评审调用（留痕 + 降级）
def test_review_pass_logged(tmp_path) -> None:
    r = review_batch_plan(
        tmp_path, _Plan(), 7, "摘要", llm=_FakeLLM("pass", "可以"), console=_Console()
    )
    assert r.verdict == "pass"
    log = (tmp_path / PLAN_REVIEW_LOG).read_text(encoding="utf-8").strip().splitlines()
    rec = json.loads(log[-1])
    assert rec["verdict"] == "pass" and rec["current_chapter"] == 7


def test_review_infeasible_feedback_carried(tmp_path) -> None:
    r = review_batch_plan(
        tmp_path, _Plan(), 7, "摘要", llm=_FakeLLM("infeasible", "与 ch07 已发生事实硬冲突"),
        console=_Console(),
    )
    assert r.verdict == "infeasible"
    assert "硬冲突" in r.feedback


def test_review_llm_down_degrades_to_unavailable(tmp_path) -> None:
    r = review_batch_plan(
        tmp_path, _Plan(), 7, "摘要", llm=_FakeLLM(None), console=_Console()
    )
    assert r.verdict == "unavailable"
    assert r.source == "degraded"


def test_review_bad_verdict_degrades(tmp_path) -> None:
    r = review_batch_plan(
        tmp_path, _Plan(), 7, "摘要", llm=_FakeLLM("maybe"), console=_Console()
    )
    assert r.verdict == "unavailable"


def test_review_disabled_flag(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NOVELAGENT_PLAN_REVIEW", "0")
    r = review_batch_plan(
        tmp_path, _Plan(), 7, "摘要", llm=_FakeLLM("revise", "x"), console=_Console()
    )
    assert r.verdict == "pass" and r.source == "disabled"


def test_review_only_unwritten_arcs_in_prompt(tmp_path) -> None:
    """已写弧线不进审稿视野（审的是计划，不是历史）。"""

    class _Capture(_FakeLLM):
        def chat(self, req):
            self.calls += 1
            self.user = req.messages[-1]["content"]
            return _FakeResp(json.dumps({"verdict": "pass", "feedback": ""}))

    class _MixedPlan:
        episode_tree = [_Arc("A0", "旧", 1, 5, "已写完", ""), _Arc("A2", "新", 8, 12, "复仇", "S01")]
        total_chapters = 100

    llm = _Capture("pass")
    review_batch_plan(tmp_path, _MixedPlan(), 7, "摘要", llm=llm, console=_Console())
    assert "旧" not in llm.user and "复仇" in llm.user


# ---------------------------------------------------------------- 升级挂起（V4.1 端到端）
def test_escalate_writes_card_and_transitions(tmp_path) -> None:
    from agent.core.engine.state_machine import State, StateMachine

    sm = StateMachine(tmp_path)
    sm.state = State.WRITING
    sm.save()
    escalate_to_checkpoint(tmp_path, "计划与已发生事实硬冲突", console=_Console())
    card = json.loads((tmp_path / ".state" / "checkpoint.json").read_text(encoding="utf-8"))
    assert card["paused"] is True
    assert "计划评审分歧" in card["risks"][0]
    sm2 = StateMachine(tmp_path)
    sm2.load()
    assert sm2.state is State.AWAITING_CHECKPOINT


# ---------------------------------------------------------------- V4.3 唯一消费点红线
def test_batch_replan_consumes_review_before_audit() -> None:
    """batch_replan 必须在 plan_managers.audit_plan 之前消费语义评审，且
    PlanReviewEscalation 穿透外层通用 except（不被 degrade 吞掉——实弹教训
    2026-10-03：内层 re-raise 后仍被外层吞，升级失去阻断权）。"""
    src = (
        Path(__file__).resolve().parents[1]
        / "src" / "agent" / "workflows" / "pipeline" / "batch_replan.py"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")
    assert "review_batch_plan(" in src
    i_review = src.index("review_batch_plan(")
    i_audit = src.index("audit_plan(project_dir")
    assert i_review < i_audit, "语义评审必须在确定性审计之前"
    # 外层：PlanReviewEscalation 的处理分支必须在通用降级 except 之前（含 raise）
    i_reraise = src.index("except PlanReviewEscalation:")
    i_generic = src.index('except Exception as e:  # noqa: BLE001 - 显性降级')
    assert i_reraise < i_generic, "升级异常的处理必须在通用降级 except 之前"
    assert "raise" in src[i_reraise:i_generic], "升级分支必须再抛出（不得吞成降级）"
