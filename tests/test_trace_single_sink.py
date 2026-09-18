"""红线 · TraceStore 唯一写入收口（2026-09-18 记账双收口事故）

**事故**：同一物理调用被两个写入者各记一次 —— provider 级收口
（``client/gateway_adapter`` → ``llm_wiring`` 的 ``llm.usage`` hook）与包装层
（``core/llmops/traced_llm.TracedLLMClient._record``）。两条记录的 token 逐字
相同、时间戳相差 ≈1ms ⇒ ``TraceStore.totals()`` **虚增 100%** ⇒ 假熔断 ⇒
自动降档 ⇒ boost 层评审维度被削。

**判据形态**刻意选「闭环对」而非「数值相等」：②③ 是一对互斥断言，共同把
「收口是否已覆盖本次调用」的判定**两个方向**都钉死 —— 判定恒真 ⇒ ③ 失败；
判定恒假 ⇒ ② 失败（回到双记）。因此回退本次修复**必然**有断言失败。

登记单：``项目文档/优化/20260918_用量记账双收口_token虚增100%.md``
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from agent.base.llm import LLMConfig, LLMResponse
from agent.client.gateway_adapter import _GatewayModelProvider
from agent.client.llm_usage import set_llm_usage_hook
from agent.core.event_sourcing.llm_wiring import wire_llm_event_hook
from agent.core.llmops.trace import TraceStore, get_tracer, set_tracer
from agent.core.llmops.traced_llm import TracedLLMClient

_TOKENS_IN, _TOKENS_OUT = 120, 80
_MODEL = "fake-model"


class _FakeProvider:
    """真实 adapter 路径上的 provider 替身：返回带真实 usage 的 LLMResponse。"""

    def __init__(self, *, fail: bool = False) -> None:
        self.config = LLMConfig(provider="fake", api_key="k", model=_MODEL, timeout=30)
        self.fail = fail
        self.calls = 0

    def chat(
        self,
        messages: Any,
        model: str,
        temperature: Any,
        max_tokens: Any,
        enable_thinking: Any,
        timeout: int,
        **kwargs: Any,
    ) -> LLMResponse:
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider boom")
        return LLMResponse(
            text="正文",
            usage={"prompt_tokens": _TOKENS_IN, "completion_tokens": _TOKENS_OUT},
        )


def _packed() -> Any:
    """adapter.complete() 所需的 packed 最小形态（messages/route/温度/估算）。"""
    return SimpleNamespace(
        messages=[{"role": "user", "content": "写"}],
        route=SimpleNamespace(model=_MODEL, card=SimpleNamespace(temperature=0.8)),
        temperature=None,
        enable_thinking=None,
        estimated_input_tokens=1,
    )


class _GatewayLikeFake:
    """最小 Gateway 替身：chat(req) 直接走**真实 adapter.complete()**。

    这样包装层的底层「确实会经 provider 级唯一收口」，与生产链路一致 ——
    若这里换成不走 adapter 的假对象，本测试就退化成「只证明没有 hook」。
    """

    def __init__(self, adapter: _GatewayModelProvider) -> None:
        self._adapter = adapter

    def chat(self, req: Any) -> Any:
        return self._adapter.complete(_packed())


class _RawResp:
    """RawResponse 风格（usage_input/usage_output）的最小替身。"""

    def __init__(self, text: str, tin: int = 12, tout: int = 8) -> None:
        self.text = text
        self.usage_input = tin
        self.usage_output = tout


class _PlainFake:
    """**非** Gateway 的底层替身：不会触发 provider 级收口。"""

    def chat(self, req: Any) -> Any:
        return _RawResp("x")


@pytest.fixture
def sink(tmp_path: Any) -> Any:
    """装配真实收口链路：wire_llm_event_hook → TraceStore（EventBus 指向 tmp）。"""
    prev_tracer = get_tracer()
    wire_llm_event_hook(str(tmp_path))
    ts = TraceStore(tmp_path)
    set_tracer(ts)
    try:
        yield ts
    finally:
        set_tracer(prev_tracer)
        set_llm_usage_hook(None)


# ---------------------------------------------------------------- ① 收口本身
def test_adapter_usage_hook_is_single_sink(sink: TraceStore) -> None:
    """真 provider 级调用 ⇒ 恰 1 个 span，且 token 等于真实用量。"""
    adapter = _GatewayModelProvider("fake", _FakeProvider())
    resp = adapter.complete(_packed())

    assert int(resp.usage_input) == _TOKENS_IN
    tot = sink.totals()
    assert tot["calls"] == 1, "一次 provider 调用必须恰好记录 1 个 span"
    assert tot["tokens_in"] == _TOKENS_IN
    assert tot["tokens_out"] == _TOKENS_OUT


# ---------------------------------------- ②③ 一对互斥断言：覆盖判定两方向
def test_traced_llm_is_pure_proxy_when_sink_covers(sink: TraceStore) -> None:
    """② 收口已记账 ⇒ 包装层**不新增** span（否则就是虚增 100% 的根因）。"""
    adapter = _GatewayModelProvider("fake", _FakeProvider())
    client = TracedLLMClient(_GatewayLikeFake(adapter), model="creative-strong")
    client.chat_creative([{"role": "user", "content": "写"}])

    tot = sink.totals()
    assert tot["calls"] == 1, "同一物理调用被两个收口各记一次 = 本次修复要切断的因果链"
    assert tot["tokens_in"] == _TOKENS_IN, "token 不得被计两次（120 → 240）"


def test_traced_llm_falls_back_when_sink_absent(sink: TraceStore) -> None:
    """③ 收口缺席（底层非 Gateway）⇒ 包装层**必须补记**（不能变成「有调用无 span」）。"""
    client = TracedLLMClient(_PlainFake(), model="m")
    client.chat_utility([{"role": "user", "content": "校验"}])

    tot = sink.totals()
    assert tot["calls"] == 1, "底层非 Gateway 时包装层是唯一写入者，漏记＝另一种失真"
    assert tot["tokens_in"] == 12 and tot["tokens_out"] == 8


# ------------------------------------------------- ④⑤ 可观测性不得降级
def test_span_use_follows_call_site(sink: TraceStore) -> None:
    """④ span.use 尊重调用方语义：收口不得把 creative/utility 硬编码成 chat。"""
    adapter = _GatewayModelProvider("fake", _FakeProvider())
    client = TracedLLMClient(_GatewayLikeFake(adapter), model="creative-strong")
    client.chat_utility([{"role": "user", "content": "校验"}])

    spans = sink.spans()
    assert len(spans) == 1
    assert spans[0].use == "utility", "用途由调用方声明，经 llm_use 传到唯一收口"


def test_span_model_is_real_model_name(sink: TraceStore) -> None:
    """⑤ 收口写**真实模型名**（此前包装层写死 creative-strong，收口写 payload model）。"""
    adapter = _GatewayModelProvider("fake", _FakeProvider())
    adapter.complete(_packed())

    span = sink.spans()[0]
    assert span.model == _MODEL, "真实模型名来自 route.model/payload，不是硬编码标签"
    assert span.meta.get("provider") == "fake"
    assert "cache_hit" in span.meta, "缓存命中留痕（HA-Eval L1）不得丢"
