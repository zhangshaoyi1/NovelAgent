"""Gateway.chat() — 唯一 LLM 出口门面（七段门禁，M1 完整版）"""

from __future__ import annotations

import time

from .models import ChatRequest, ChatResponse, ErrorClass
from .packer import BudgetError, Packer
from .providers.registry import ProviderRegistry
from .rate_limiter import RateLimiter, SemanticCache
from .request_gate import RequestGate
from .response_gate import MetricsSink, ResponseGate
from .router import ComplexityRouter, RoutePolicy


class Gateway:
    """模型调用门面：全系统唯一 LLM 出口

    七段门禁流程（M1 完整版）：
    ① RequestGate.admit()     — 预算预扣 + 缓存命中
    ② Router.decide()          — 按 hint 分档选模型
    ③ Packer.pack()            — 真实 token 计数 + 压缩 + 指纹
    ④ ProviderRegistry.invoke() — 调用 + 故障转移
    ⑤ ResponseGate.admit()     — 结构化输出 repair
    ⑥ MetricsSink.record()     — 成本/延迟/路由归因
    ⑦ 返回 ChatResponse
    """

    def __init__(
        self,
        request_gate: RequestGate | None = None,
        router: RoutePolicy | None = None,
        packer: Packer | None = None,
        registry: ProviderRegistry | None = None,
        response_gate: ResponseGate | None = None,
        metrics_sink: MetricsSink | None = None,
        rate_limiter: RateLimiter | None = None,
        semantic_cache: SemanticCache | None = None,
    ) -> None:
        self._request_gate = request_gate or RequestGate()
        self._router = router or ComplexityRouter()
        self._packer = packer or Packer()
        self._registry = registry or ProviderRegistry()
        self._response_gate = response_gate or ResponseGate()
        self._metrics_sink = metrics_sink or MetricsSink()
        self._rate_limiter = rate_limiter or RateLimiter()
        self._semantic_cache = semantic_cache or SemanticCache()

    @property
    def registry(self) -> ProviderRegistry:
        return self._registry

    @property
    def metrics_sink(self) -> MetricsSink:
        return self._metrics_sink

    @property
    def response_gate(self) -> ResponseGate:
        return self._response_gate

    @property
    def semantic_cache(self) -> SemanticCache:
        return self._semantic_cache

    @property
    def rate_limiter(self) -> RateLimiter:
        return self._rate_limiter

    def chat(self, req: ChatRequest) -> ChatResponse:
        """七段门禁全流程"""
        # ① RequestGate：预算预扣 + 缓存
        decision = self._request_gate.admit(req)
        if not decision.ok:
            raise GatewayError(ErrorClass.BUDGET, decision.reject_reason)
        if decision.cache_hit is not None:
            return decision.cache_hit

        # 限流（非 quality_critical 任务）
        if not req.hint.quality_critical:
            if not self._rate_limiter.allow(key=req.run_id or "default"):
                raise GatewayError(
                    ErrorClass.RATE_LIMIT,
                    f"限流中，预估等待 {self._rate_limiter.wait_time():.1f}s",
                )

        # 语义缓存查找
        cached = self._semantic_cache.lookup(req)
        if cached is not None:
            return cached

        # ② Router：按 hint 选模型
        route = self._router.decide(req, self._registry.available(), decision.budget)

        # ③ Packer：真实计数 + 指纹
        try:
            packed = self._packer.pack(req, route)
        except BudgetError as e:
            raise GatewayError(ErrorClass.BUDGET, str(e)) from e

        # ④ ProviderRegistry：调用 + 故障转移
        t0 = time.monotonic()
        success = True
        error_msg = ""
        try:
            raw = self._registry.invoke(route, packed)
        except Exception as e:
            success = False
            error_msg = str(e)
            raise GatewayError(ErrorClass.TRANSIENT, str(e)) from e
        finally:
            elapsed = (time.monotonic() - t0) * 1000.0
            if not success:
                self._metrics_sink.record(
                    run_id=req.run_id,
                    provider=route.provider,
                    model=route.model,
                    strategy=route.strategy,
                    input_tokens=packed.estimated_input_tokens,
                    output_tokens=0,
                    latency_ms=elapsed,
                    success=False,
                    error=error_msg,
                )
        elapsed = (time.monotonic() - t0) * 1000.0

        # 构造响应
        resp = ChatResponse(
            text=raw.text,
            provider=raw.provider or route.provider,
            model=raw.model or route.model,
            context_fingerprint=packed.context_fingerprint,
            usage_input=raw.usage_input or packed.estimated_input_tokens,
            usage_output=raw.usage_output,
            elapsed_ms=raw.elapsed_ms or elapsed,
        )

        # ⑤ ResponseGate：结构化输出 repair
        # extra 可能被调用方显式置 None（dataclass 不校验），防御性兜底
        resp = self._response_gate.admit(resp, expected_format=(req.extra or {}).get("format", ""))

        # ⑥ MetricsSink：成本/延迟/路由归因
        cost_cents = route.card.cost_per_1k_input_cents * resp.usage_input / 1000.0 + route.card.cost_per_1k_output_cents * resp.usage_output / 1000.0
        self._metrics_sink.record(
            run_id=req.run_id,
            provider=resp.provider,
            model=resp.model,
            strategy=route.strategy,
            input_tokens=resp.usage_input,
            output_tokens=resp.usage_output,
            latency_ms=resp.elapsed_ms,
            cost_cents=round(cost_cents, 4),
        )

        # 语义缓存写入
        self._semantic_cache.store(req, resp)

        # ⑦ 返回
        return resp


class GatewayError(Exception):
    """Gateway 运行时错误"""

    def __init__(self, error_class: ErrorClass, message: str = "") -> None:
        self.error_class = error_class
        super().__init__(message or error_class.value)