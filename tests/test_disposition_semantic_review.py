"""处置方案语义终审红线（登记单 20261001_信任链与叙事上限六项能力·子项 2）。

守卫三条验收标准：
- **V2.1 真把关者**：终审被真实调用，裁决（含否决理由）留痕
  ``quality_audit.jsonl``（pass_scope=disposition_review），veto 走
  守门器拒绝 → escalated_reason（→ pipeline failure 事件 → Web 可见）。
- **V2.2 降级不裸奔**：终审 LLM 不可用 → verdict=unavailable → 按现状四道
  守门语义放行，degrade 留痕，处置链不中断。
- **V2.3 未经终审不得执行**：gate 携带终审 fn 时，ROLLBACK_REWRITE 未过
  第五道即执行被守门器拒绝；veto 阻断 trigger_rollback（升级人工）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.core.quality.disposition import (
    Action,
    Authorization,
    DispositionGate,
    DispositionPlan,
)
from agent.core.quality.disposition_review import DispositionSemanticReviewer


# ---------------------------------------------------------------- 夹具
class _Dim:
    def __init__(self, name: str, label: str, value: float = 3.0):
        self.name = name
        self.label = label
        self.value = value
        self.required = True
        self.confidence = 1.0
        self.description = "测试失败明细"

    @property
    def spec(self):
        from agent.core.quality.dimension_registry import (
            EvalTiming,
            Repairability,
            spec_for,
        )

        return spec_for(self.name)


class _Console:
    def print(self, *args, **kwargs):  # noqa: D102
        pass


class _FakeResp:
    def __init__(self, text: str):
        self.text = text


class _FakeLLM:
    """可编程的假 gateway：chat() 返回预设 verdict JSON；抛异常模拟不可用。"""

    def __init__(self, verdict: str | None = "allow", reason: str = ""):
        self.verdict = verdict
        self.reason = reason
        self.calls = 0
        self.last_request = None

    def chat(self, req):
        self.calls += 1
        self.last_request = req
        if self.verdict is None:
            raise RuntimeError("网关不可达")
        return _FakeResp(json.dumps({"verdict": self.verdict, "reason": self.reason}))


def _rollback_plan() -> DispositionPlan:
    return DispositionPlan(
        action=Action.ROLLBACK_REWRITE,
        reason="硬指标不达标：人设稳定",
        dims=[_Dim("character_stability_high", "人设稳定")],
    )


def _allow_auth() -> Authorization:
    return Authorization(True, "终审放行")


# ---------------------------------------------------------------- V2.3 gate 第五道
def test_gate_semantic_veto_denies_rollback() -> None:
    calls: list[tuple] = []

    def _fn(plan, chapters):
        calls.append((plan.action, chapters))
        return Authorization(False, "弧光登记显示该转变是设计内成长")

    gate = DispositionGate(semantic_review_fn=_fn)
    auth = gate.authorize(_rollback_plan(), chapters=5, double_evidence=True)
    assert not auth.ok
    assert "语义终审否决" in auth.reason
    assert "弧光登记" in auth.reason
    assert calls == [(Action.ROLLBACK_REWRITE, 5)]


def test_gate_semantic_allow_passes_through() -> None:
    gate = DispositionGate(semantic_review_fn=lambda p, c: _allow_auth())
    auth = gate.authorize(_rollback_plan(), chapters=5, double_evidence=True)
    assert auth.ok


def test_gate_without_review_fn_keeps_legacy_semantics() -> None:
    """未接线的 gate 保持旧四道语义（直构造兼容；生产入口由红线钉住必须接线）。"""
    gate = DispositionGate()
    auth = gate.authorize(_rollback_plan(), chapters=5, double_evidence=True)
    assert auth.ok


def test_gate_review_fn_exception_denies() -> None:
    """终审执行异常按拒绝处理（保守），不得炸守门器。"""
    def _boom(plan, chapters):
        raise RuntimeError("boom")

    gate = DispositionGate(semantic_review_fn=_boom)
    auth = gate.authorize(_rollback_plan(), chapters=5, double_evidence=True)
    assert not auth.ok
    assert "终审执行异常" in auth.reason


# ---------------------------------------------------------------- V2.1 真把关者 + 留痕
def test_reviewer_veto_recorded_in_audit(tmp_path) -> None:
    llm = _FakeLLM("veto", "该'崩坏'是弧光登记的成长轨迹，回滚会破坏合法设计")
    r = DispositionSemanticReviewer(llm, project_dir=tmp_path, console=_Console())
    v = r.review(_rollback_plan(), chapters=5)
    assert v.decision == "veto"
    assert not v.ok
    assert llm.calls == 1
    audit_lines = (tmp_path / ".state" / "quality_audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
    rec = json.loads(audit_lines[-1])
    assert rec["pass_scope"] == "disposition_review"
    d = rec["dimensions"][0]
    assert d["name"] == "disposition_semantic_review"
    assert d["value"] == 0.0
    assert "弧光登记" in d["issues"][0]


def test_reviewer_allow_recorded_and_gate_integrated(tmp_path) -> None:
    llm = _FakeLLM("allow", "病灶在窗口内，证据自洽")
    r = DispositionSemanticReviewer(llm, project_dir=tmp_path, console=_Console())
    gate = DispositionGate(semantic_review_fn=r.gate_fn())
    auth = gate.authorize(_rollback_plan(), chapters=5, double_evidence=True)
    assert auth.ok
    assert llm.calls == 1
    rec = json.loads(
        (tmp_path / ".state" / "quality_audit.jsonl").read_text(encoding="utf-8").strip().splitlines()[-1]
    )
    assert rec["dimensions"][0]["value"] == 1.0


def test_reviewer_supervisor_alerts_reach_prompt(tmp_path) -> None:
    """Supervisor advisory 告警作为终审证据进入裁决输入（证据源接入）。"""
    seen: dict = {}

    class _CapturingLLM(_FakeLLM):
        def chat(self, req):
            self.calls += 1
            self.last_request = req
            seen["messages"] = req.messages
            return _FakeResp(json.dumps({"verdict": "allow", "reason": ""}))

    llm = _CapturingLLM("allow")
    r = DispositionSemanticReviewer(
        llm,
        project_dir=tmp_path,
        console=_Console(),
        supervisor_alerts_fn=lambda: ["[critical/plot] 连续 10 章无推进"],
    )
    r.review(_rollback_plan(), chapters=5)
    user_msg = seen["messages"][-1]["content"]
    assert "连续 10 章无推进" in user_msg


# ---------------------------------------------------------------- V2.2 降级不裸奔
def test_reviewer_llm_unavailable_degrades_to_allow(tmp_path) -> None:
    llm = _FakeLLM(None)  # 网关不可达
    r = DispositionSemanticReviewer(llm, project_dir=tmp_path, console=_Console())
    v = r.review(_rollback_plan(), chapters=5)
    assert v.decision == "unavailable"
    assert v.source == "degraded"
    assert v.ok, "LLM 不可用必须降级为现状行为（四道守门语义），不得引入新裸奔面或新阻断"
    # 降级同样留痕
    rec = json.loads(
        (tmp_path / ".state" / "quality_audit.jsonl").read_text(encoding="utf-8").strip().splitlines()[-1]
    )
    assert rec["dimensions"][0]["source"] == "degraded"


def test_reviewer_disabled_flag(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NOVELAGENT_DISPOSITION_REVIEW", "0")
    r = DispositionSemanticReviewer(_FakeLLM("veto", "x"), project_dir=tmp_path, console=_Console())
    v = r.review(_rollback_plan(), chapters=5)
    assert v.decision == "disabled"
    assert v.ok


def test_reviewer_bad_verdict_degrades(tmp_path) -> None:
    llm = _FakeLLM("maybe")  # 非法 verdict
    r = DispositionSemanticReviewer(llm, project_dir=tmp_path, console=_Console())
    v = r.review(_rollback_plan(), chapters=5)
    assert v.decision == "unavailable"


# ---------------------------------------------------------------- V2.3 Evaluator 接线
def test_evaluator_veto_blocks_rollback(tmp_path) -> None:
    """端到端：硬指标失败 → 处置层授权回滚 → 终审 veto → trigger_rollback 不执行，
    escalated_reason 携带否决理由（→ pipeline failure 事件 → Web 运行控制台可见）。"""
    from tests.test_evaluator_rollback import (
        _dim,
        _fail_report,
        _pass_report,
    )

    from agent.agents.evaluator import EvaluatorAgent, RepairPlan

    evalr = EvaluatorAgent(
        str(tmp_path),
        rollback_window=5,
        max_rollback_attempts=2,
    )
    calls = {"rollback": 0}

    def _fake_evaluate_once():
        return _fail_report() if calls["rollback"] == 0 else _pass_report()

    def _fake_trigger_rollback():
        calls["rollback"] += 1
        return RepairPlan(target_chapter=1, chapters_to_rewrite=[1], reason="t", rolled_back=True)

    evalr._evaluate_once = _fake_evaluate_once  # type: ignore[method-assign]
    evalr.trigger_rollback = _fake_trigger_rollback  # type: ignore[method-assign]

    veto_reason = "弧光登记显示该转变是设计内成长"

    class _StubReviewer:
        def gate_fn(self):
            def _fn(plan, chapters):
                return Authorization(False, veto_reason)
            return _fn

    evalr.semantic_reviewer = _StubReviewer()
    evalr._gate.semantic_review_fn = _StubReviewer().gate_fn()

    report = evalr.evaluate_with_repair(lambda chapters: None)
    assert calls["rollback"] == 0, "终审 veto 后不得执行回滚"
    assert report.escalated
    assert "语义终审否决" in (report.escalated_reason or "")


def test_evaluator_default_pipeline_wiring() -> None:
    """红线：pipeline 的 _ensure_evaluator 必须接线语义终审（防"看起来接了"）。"""
    import ast
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[1]
        / "src" / "agent" / "workflows" / "pipeline" / "agentic_pipeline_agents.py"
    ).read_text(encoding="utf-8")
    assert "DispositionSemanticReviewer(" in src
    assert "semantic_reviewer=semantic_reviewer" in src
