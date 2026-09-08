"""RateLimiter + 语义缓存：Gateway 限流与缓存

M1 新增模块。
"""

from __future__ import annotations

import hashlib  # noqa: F401 - 保留：历史导入，llmagent 包对外未冻结
import time
from dataclasses import dataclass, field, replace
from typing import Any

from .cache_policy import DEFAULT_TTL_S, _full_key, decide
from .models import ChatRequest, ChatResponse


@dataclass
class RateLimitBucket:
    """令牌桶"""

    tokens: float
    last_refill: float
    capacity: float
    refill_rate: float  # tokens per second


class RateLimiter:
    """限流器：基于令牌桶

    非 quality_critical 任务可降速。
    """

    def __init__(self) -> None:
        self._buckets: dict[str, RateLimitBucket] = {}

    def _get_bucket(self, key: str, capacity: float = 60.0, refill_rate: float = 1.0) -> RateLimitBucket:
        if key not in self._buckets:
            self._buckets[key] = RateLimitBucket(
                tokens=capacity, last_refill=time.monotonic(), capacity=capacity, refill_rate=refill_rate,
            )
        return self._buckets[key]

    def _refill(self, bucket: RateLimitBucket) -> None:
        now = time.monotonic()
        elapsed = now - bucket.last_refill
        bucket.tokens = min(bucket.capacity, bucket.tokens + elapsed * bucket.refill_rate)
        bucket.last_refill = now

    def allow(self, key: str = "default", cost: float = 1.0, capacity: float = 60.0, refill_rate: float = 1.0) -> bool:
        """检查是否允许请求"""
        bucket = self._get_bucket(key, capacity, refill_rate)
        self._refill(bucket)
        if bucket.tokens >= cost:
            bucket.tokens -= cost
            return True
        return False

    def wait_time(self, key: str = "default", cost: float = 1.0) -> float:
        """预估等待时间（秒）"""
        bucket = self._buckets.get(key)
        if bucket is None:
            return 0.0
        if bucket.tokens >= cost:
            return 0.0
        deficit = cost - bucket.tokens
        return deficit / bucket.refill_rate if bucket.refill_rate > 0 else float("inf")


class SemanticCache:
    """语义缓存（精确匹配；裁决统一委托 ``cache_policy.decide``）

    2026-09-08（HA-Eval L1）两处修正：

    1. **键不再采样末 200 字符**：改为全量 content 哈希（见 ``cache_policy._full_key``），
       消除「差异在 prompt 开头 → 键碰撞 → 多个维度共用同一份响应」的事故根因。
    2. **默认拒绝**：仅当调用方显式声明 ``cache_class=DETERMINISTIC`` 才缓存；
       ``quality_critical`` 依旧不缓存。判定类（评分/审查）结果不再被复用。

    命中时返回 ``replace(resp, cache_hit=True, cache_key=key)``，使调用方可观测。
    """

    def __init__(self, max_size: int = 100, ttl_s: float = DEFAULT_TTL_S) -> None:
        self._cache: dict[str, tuple[ChatResponse, float]] = {}
        self._max_size = max_size
        self._ttl_s = ttl_s

    def _make_key(self, req: ChatRequest) -> str:
        """生成缓存键（全量哈希）。保留方法名以兼容既有调用。"""
        return _full_key(req)

    def lookup(self, req: ChatRequest) -> ChatResponse | None:
        """查找缓存；未命中 / 不允许缓存返回 None。"""
        d = decide(req, ttl_s=self._ttl_s)
        if not d.enabled:
            return None
        entry = self._cache.get(d.key)
        if entry is None:
            return None
        resp, ts = entry
        if time.monotonic() - ts > self._ttl_s:
            del self._cache[d.key]
            return None
        return replace(resp, cache_hit=True, cache_key=d.key)

    def store(self, req: ChatRequest, resp: ChatResponse) -> None:
        """存入缓存；不允许缓存的请求直接忽略。"""
        d = decide(req, ttl_s=self._ttl_s)
        if not d.enabled:
            return
        if len(self._cache) >= self._max_size:
            # 淘汰最旧的
            oldest = min(self._cache.keys(), key=lambda k: self._cache[k][1])
            del self._cache[oldest]
        self._cache[d.key] = (resp, time.monotonic())