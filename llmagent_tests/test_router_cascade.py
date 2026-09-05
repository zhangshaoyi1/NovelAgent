"""路由增强测试：O4 hint 校准 + O5 Cascade 先小后大"""

from __future__ import annotations

import json

from llmagent.gateway.models import (
    BudgetSnapshot,
    ChatRequest,
    ChatResponse,
    HintComplexity,
    ModelCard,
    PackedRequest,
    RawResponse,
    TaskHint,
)
from llmagent.gateway.router import CascadeRoute, ComplexityRouter, HintCalibrator


def _cards() -> list[ModelCard]:
    return [
        ModelCard(provider="openai", model="gpt-4o", cost_per_1k_input_cents=0.5,
                  cost_per_1k_output_cents=1.5, context_window=128000),
        ModelCard(provider="openai", model="gpt-4o-mini", cost_per_1k_input_cents=0.015,
                  cost_per_1k_output_cents=0.06, context_window=128000),
        ModelCard(provider="qwen", model="qwen-max", cost_per_1k_input_cents=0.2,
                  cost_per_1k_output_cents=0.6, context_window=32000),
        ModelCard(provider="qwen", model="qwen-plus", cost_per_1k_input_cents=0.08,
                  cost_per_1k_output_cents=0.24, context_window=32000),
    ]


# ---- O4：HintCalibrator ----


class TestHintCalibrator:
    def test_no_history_keeps_declared(self, tmp_path):
        cal = HintCalibrator(persist_path=tmp_path / "cal.json")
        assert cal.suggest("m5", HintComplexity.simple) is HintComplexity.simple

    def test_bump_after_enough_samples(self, tmp_path):
        cal = HintCalibrator(persist_path=tmp_path / "cal.json")
        for _ in range(3):
            cal.record("m5", HintComplexity.simple, 5000)
        assert cal.suggest("m5", HintComplexity.simple) is HintComplexity.complex
        # 其他标签不受影响
        assert cal.suggest("other", HintComplexity.simple) is HintComplexity.simple

    def test_small_outputs_do_not_bump(self, tmp_path):
        cal = HintCalibrator(persist_path=tmp_path / "cal.json")
        for _ in range(3):
            cal.record("m5", HintComplexity.simple, 100)
        assert cal.suggest("m5", HintComplexity.simple) is HintComplexity.simple

    def test_persistence_across_instances(self, tmp_path):
        path = tmp_path / "cal.json"
        cal1 = HintCalibrator(persist_path=path)
        for _ in range(3):
            cal1.record("m5", HintComplexity.simple, 9000)
        cal2 = HintCalibrator(persist_path=path)
        assert cal2.suggest("m5", HintComplexity.simple) is HintComplexity.complex
        assert json.loads(path.read_text(encoding="utf-8"))["m5"]["simple"][-3:] == [9000, 9000, 9000]

    def test_router_uses_calibrated_complexity(self, tmp_path):
        cal = HintCalibrator(persist_path=tmp_path / "cal.json")
        for _ in range(3):
            cal.record("chapter_draft", HintComplexity.simple, 8000)
        router = ComplexityRouter(calibrator=cal)
        req = ChatRequest(
            messages=[{"role": "user", "content": "x"}],
            hint=TaskHint(complexity=HintComplexity.simple),
            extra={"task": "chapter_draft"},
        )
        decision = router.decide(req, _cards(), None)
        assert decision.strategy == "complexity(calibrated)"
        assert decision.model == "gpt-4o"  # 抬升后走大模型

    def test_router_record_writes_history(self, tmp_path):
        cal = HintCalibrator(persist_path=tmp_path / "cal.json")
        router = ComplexityRouter(calibrator=cal)
        req = ChatRequest(
            messages=[{"role": "user", "content": "x"}],
            hint=TaskHint(complexity=HintComplexity.simple),
            extra={"task": "m5"},
        )
        resp = ChatResponse(text="t", provider="p", model="m", usage_output=123)
        router.record(req, resp)
        assert cal._history["m5"]["simple"] == [123]


# ---- O5：CascadeRoute ----


class TestCascadeRoute:
    def test_normal_request_not_cascade(self):
        inner = ComplexityRouter()
        route = CascadeRoute(inner)
        req = ChatRequest(messages=[{"role": "user", "content": "x"}])
        d = route.decide(req, _cards(), None)
        assert d.strategy == "complexity"  # 未声明 cascade，行为与 inner 一致

    def test_cascade_first_hop_is_cheapest(self):
        route = CascadeRoute()
        req = ChatRequest(
            messages=[{"role": "user", "content": "x"}],
            hint=TaskHint(complexity=HintComplexity.complex),
            extra={"cascade": True, "verify": lambda r: False},
        )
        d = route.decide(req, _cards(), None)
        assert d.strategy == "cascade_first"
        assert d.model == "gpt-4o-mini"  # 最便宜

    def test_cascade_ignored_without_verify(self):
        route = CascadeRoute()
        req = ChatRequest(messages=[{"role": "user", "content": "x"}],
                          extra={"cascade": True})
        assert route.decide(req, _cards(), None).strategy == "complexity"

    def test_cascade_respects_explicit_model(self):
        route = CascadeRoute()
        req = ChatRequest(messages=[{"role": "user", "content": "x"}],
                          extra={"cascade": True, "verify": lambda r: True,
                                 "model": "qwen-max"})
        d = route.decide(req, _cards(), None)
        assert d.strategy == "explicit"

    def test_escalate_picks_strongest(self):
        cards = _cards()
        current = next(c for c in cards if c.model == "gpt-4o-mini")
        d = CascadeRoute.escalate(cards, current, None)
        assert d is not None
        assert d.strategy == "cascade_escalate"
        assert d.model == "gpt-4o"  # 上下文窗口最大

    def test_escalate_none_when_already_strongest(self):
        cards = _cards()
        strongest = max(cards, key=lambda c: c.context_window)
        assert CascadeRoute.escalate(cards, strongest, None) is None
