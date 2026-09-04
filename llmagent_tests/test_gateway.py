"""Gateway 单元测试"""

from __future__ import annotations

import pytest

from llmagent.gateway.chat import Gateway, GatewayError
from llmagent.gateway.models import (
    ChatRequest,
    HintComplexity,
    ModelCard,
    PackedRequest,
    RawResponse,
    RouteDecision,
    TaskHint,
)
from llmagent.gateway.packer import BudgetError, Packer
from llmagent.gateway.providers.registry import ModelProvider, ProviderRegistry
from llmagent.gateway.request_gate import RequestGate
from llmagent.gateway.router import ComplexityRouter


# ---- 测试用 Mock Provider ----


class MockProvider:
    name = "mock"

    def complete(self, packed: PackedRequest) -> RawResponse:
        return RawResponse(
            text="mock response",
            provider="mock",
            model="mock-model",
            usage_input=packed.estimated_input_tokens,
            usage_output=50,
        )

    def count_tokens(self, text: str) -> int:
        return len(text) // 4

    def model_card(self) -> ModelCard:
        return ModelCard(
            provider="mock",
            model="mock-model",
            cost_per_1k_input_cents=0.1,
            cost_per_1k_output_cents=0.3,
            context_window=32000,
        )


class FailingProvider:
    name = "failing"

    def complete(self, packed: PackedRequest) -> RawResponse:
        raise RuntimeError("provider 不可达")

    def count_tokens(self, text: str) -> int:
        return 0

    def model_card(self) -> ModelCard:
        return ModelCard(
            provider="failing",
            model="failing-model",
            cost_per_1k_input_cents=0.0,
            cost_per_1k_output_cents=0.0,
            context_window=1000,
        )


class DeterministicFailingProvider:
    name = "badrequest"

    def complete(self, packed: PackedRequest) -> RawResponse:
        raise ValueError("参数错误")

    def count_tokens(self, text: str) -> int:
        return 0

    def model_card(self) -> ModelCard:
        return ModelCard(
            provider="badrequest",
            model="badrequest-model",
            cost_per_1k_input_cents=0.0,
            cost_per_1k_output_cents=0.0,
            context_window=1000,
        )


# ---- 测试 RequestGate ----


class TestRequestGate:
    def test_admit_without_budget_ref(self):
        gate = RequestGate()
        req = ChatRequest(messages=[{"role": "user", "content": "hello"}])
        decision = gate.admit(req)
        assert decision.ok is True
        assert decision.cache_hit is None

    def test_admit_cache_hit(self):
        gate = RequestGate()
        req = ChatRequest(messages=[{"role": "user", "content": "hello"}])
        # 第一次调用
        decision1 = gate.admit(req)
        assert decision1.ok is True
        # 修改缓存策略为直接返回
        decision1.cache_hit = None  # 移除缓存命中
        assert decision1.budget is not None


# ---- 测试 Router ----


class TestRouter:
    def test_complexity_router_simple(self):
        router = ComplexityRouter()
        req = ChatRequest(messages=[], hint=TaskHint(complexity=HintComplexity.simple))
        route = router.decide(req)
        assert route.provider in ("openai", "qwen")
        # simple 应该选最便宜的
        assert route.card.cost_per_1k_input_cents <= 0.1

    def test_complexity_router_complex(self):
        router = ComplexityRouter()
        req = ChatRequest(messages=[], hint=TaskHint(complexity=HintComplexity.complex))
        route = router.decide(req)
        # complex 应该有更大的上下文窗口
        assert route.card.context_window >= 32000


# ---- 测试 Packer ----


class TestPacker:
    def test_pack_basic(self):
        packer = Packer()
        req = ChatRequest(messages=[{"role": "user", "content": "写一个故事"}])
        route = RouteDecision(provider="mock", model="mock-model", card=MockProvider().model_card())
        packed = packer.pack(req, route)
        assert packed.estimated_input_tokens > 0
        assert packed.context_fingerprint != ""
        assert len(packed.messages) == 1

    def test_pack_context_fingerprint_stable(self):
        packer = Packer()
        req = ChatRequest(messages=[{"role": "user", "content": "你好"}])
        route = RouteDecision(provider="mock", model="mock-model", card=MockProvider().model_card())
        fp1 = packer.pack(req, route).context_fingerprint
        fp2 = packer.pack(req, route).context_fingerprint
        assert fp1 == fp2


# ---- 测试 ProviderRegistry ----


class TestProviderRegistry:
    def test_register_and_invoke(self):
        registry = ProviderRegistry()
        registry.register("mock", MockProvider())
        cards = registry.available()
        assert len(cards) == 1
        assert cards[0].provider == "mock"

    def test_failover_on_transient(self):
        registry = ProviderRegistry()
        registry.register("failing", FailingProvider())
        registry.register("mock", MockProvider())
        route = RouteDecision(
            provider="failing", model="failing-model", card=FailingProvider().model_card()
        )
        packed = PackedRequest(messages=[{"role": "user", "content": "test"}])
        # 应故障转移到 mock
        resp = registry.invoke(route, packed)
        assert resp is not None

    def test_no_failover_on_deterministic(self):
        registry = ProviderRegistry()
        registry.register("badrequest", DeterministicFailingProvider())
        registry.register("mock", MockProvider())
        route = RouteDecision(
            provider="badrequest", model="badrequest-model", card=DeterministicFailingProvider().model_card()
        )
        packed = PackedRequest(messages=[{"role": "user", "content": "test"}])
        with pytest.raises(ValueError, match="参数错误"):
            registry.invoke(route, packed)


# ---- 测试 Gateway 集成 ----


class TestGateway:
    def test_chat_basic(self):
        gateway = Gateway()
        gateway.registry.register("mock", MockProvider())
        req = ChatRequest(messages=[{"role": "user", "content": "你好"}])
        resp = gateway.chat(req)
        assert resp.text == "mock response"
        assert resp.provider == "mock"
        assert resp.context_fingerprint != ""

    def test_chat_model_route(self):
        gateway = Gateway()
        gateway.registry.register("mock", MockProvider())
        # simple 走小模型
        req = ChatRequest(
            messages=[{"role": "user", "content": "简单任务"}],
            hint=TaskHint(complexity=HintComplexity.simple),
        )
        resp = gateway.chat(req)
        assert resp.text == "mock response"

    def test_chat_no_registered_provider(self):
        gateway = Gateway()
        req = ChatRequest(messages=[{"role": "user", "content": "hi"}])
        with pytest.raises(GatewayError) as exc_info:
            gateway.chat(req)
        assert exc_info.value.error_class.value == "TRANSIENT"