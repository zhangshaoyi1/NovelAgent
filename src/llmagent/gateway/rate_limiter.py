"""RateLimiter + 语义缓存：Gateway 限流与缓存

M1 新增模块。
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any

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
    """语义缓存（M1 简化版：精确匹配 + 前缀匹配）

    quality_critical 任务不走缓存。
    """

    def __init__(self, max_size: int = 100, ttl_s: float = 300.0) -> None:
        self._cache: dict[str, tuple[ChatResponse, float]] = {}
        self._max_size = max_size
        self._ttl_s = ttl_s

    def _make_key(self, req: ChatRequest) -> str:
        """生成缓存键"""
        raw = "|".join(
            m.get("role", "") + ":" + m.get("content", "")[-200:]
            for m in req.messages[-2:]
        )
        raw += f"|hint={req.hint.complexity.value}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def lookup(self, req: ChatRequest) -> ChatResponse | None:
        """查找缓存"""
        if req.hint.quality_critical:
            return None  # quality_critical 不走缓存
        key = self._make_key(req)
        entry = self._cache.get(key)
        if entry is None:
            return None
        resp, ts = entry
        if time.monotonic() - ts > self._ttl_s:
            del self._cache[key]
            return None
        return resp

    def store(self, req: ChatRequest, resp: ChatResponse) -> None:
        """存入缓存"""
        if req.hint.quality_critical:
            return
        if len(self._cache) >= self._max_size:
            # 淘汰最旧的
            oldest = min(self._cache.keys(), key=lambda k: self._cache[k][1])
            del self._cache[oldest]
        key = self._make_key(req)
        self._cache[key] = (resp, time.monotonic())