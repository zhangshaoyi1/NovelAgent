"""HA-Eval L1 · 缓存语义层回归测试

覆盖 2026-09-08《五灵破归档》事故根因：``SemanticCache`` 原先把指纹采样在
messages 末 200 字符，而评估 prompt 的唯一差异（维度标签）在**开头** →
五个维度算出同一个 key → 共用同一份响应 → 五维取值恒等 → 量纲串用 →
required 硬门禁误判 → 误触发回滚重写。

本文件锁死四条护栏：
1. 前缀差异不碰撞；2. 默认拒绝（JUDGMENT / 未声明不缓存）；
3. quality_critical 不缓存；4. 命中留痕（cache_hit / cache_key）。
"""

from __future__ import annotations

import time

import pytest

from llmagent.gateway.cache_policy import (
    DEFAULT_TTL_S,
    CacheClass,
    _full_key,
    decide,
)
from llmagent.gateway.models import (
    ChatRequest,
    ChatResponse,
    HintComplexity,
    TaskHint,
)
from llmagent.gateway.rate_limiter import SemanticCache


def _req(
    user_content: str,
    *,
    quality_critical: bool = False,
    cache_class: str | None = None,
    temperature: float | None = 0.2,
    model: str | None = None,
) -> ChatRequest:
    extra: dict[str, object] = {}
    if cache_class is not None:
        extra["cache_class"] = cache_class
    if model is not None:
        extra["model"] = model
    return ChatRequest(
        messages=[
            {"role": "system", "content": "你是苛刻的网文总编"},
            {"role": "user", "content": user_content},
        ],
        hint=TaskHint(
            complexity=HintComplexity.simple,
            quality_critical=quality_critical,
            temperature=temperature,
        ),
        extra=extra,
    )


def _resp(text: str = '{"value": 3}') -> ChatResponse:
    return ChatResponse(text=text, provider="p", model="m", usage_output=10)


# ============================================================
# 1. 键：前缀 / 后缀 / 采样参数差异均不碰撞
# ============================================================
class TestFullKey:
    def test_prefix_differs_no_collision(self):
        """回归本次事故：唯一差异在 prompt 开头。

        事故原 prompt 结构 = 「维度标签（开头，五维不同）+ 8000 字正文（相同）」。
        旧键算法只采末 200 字符 → 五维同键 → 串值。
        """
        body = "正文" * 4000  # 远超旧实现的 200 字符采样窗口
        keys = {
            _full_key(_req(f"请评估「{label}」维度。\n\n{body}"))
            for label in ("人设稳定性", "设定一致性", "逻辑漏洞", "连贯性", "追读力")
        }
        assert len(keys) == 5, "五个维度标签不同，必须算出 5 个不同的键"

    def test_suffix_differs_no_collision(self):
        body = "正文" * 4000
        a = _full_key(_req(f"评估维度A。\n\n{body}尾部甲"))
        b = _full_key(_req(f"评估维度A。\n\n{body}尾部乙"))
        assert a != b

    def test_temperature_differs_no_collision(self):
        a = _full_key(_req("同样的提示词", temperature=0.2))
        b = _full_key(_req("同样的提示词", temperature=0.6))
        assert a != b, "采样温度不同，结果本就可能不同，不可复用"

    def test_model_differs_no_collision(self):
        a = _full_key(_req("同样的提示词", model="m1"))
        b = _full_key(_req("同样的提示词", model="m2"))
        assert a != b

    def test_same_request_same_key(self):
        a = _full_key(_req("完全一致", temperature=0.2, model="m1"))
        b = _full_key(_req("完全一致", temperature=0.2, model="m1"))
        assert a == b, "同输入必须同键，否则缓存永不命中"

    def test_message_count_matters(self):
        """[a][bc] 与 [ab][c] 不得拼接出相同字节流。"""
        r1 = ChatRequest(messages=[{"role": "u", "content": "a"},
                                   {"role": "u", "content": "bc"}],
                         hint=TaskHint(quality_critical=False))
        r2 = ChatRequest(messages=[{"role": "u", "content": "ab"},
                                   {"role": "u", "content": "c"}],
                         hint=TaskHint(quality_critical=False))
        assert _full_key(r1) != _full_key(r2)


# ============================================================
# 2. 裁决：默认拒绝
# ============================================================
class TestDecide:
    def test_quality_critical_never_cached(self):
        d = decide(_req("x", quality_critical=True,
                        cache_class=CacheClass.DETERMINISTIC.value))
        assert d.enabled is False
        assert d.reason == "quality_critical"

    def test_unset_cache_class_rejected(self):
        """本次事故的第二道失守点：utility 通道未声明即可缓存。"""
        d = decide(_req("x"))
        assert d.enabled is False
        assert "not_opted_in" in d.reason

    def test_judgment_rejected(self):
        d = decide(_req("x", cache_class=CacheClass.JUDGMENT.value))
        assert d.enabled is False

    def test_creative_rejected(self):
        d = decide(_req("x", cache_class=CacheClass.CREATIVE.value))
        assert d.enabled is False

    def test_deterministic_opted_in(self):
        d = decide(_req("x", cache_class=CacheClass.DETERMINISTIC.value))
        assert d.enabled is True
        assert d.key
        assert d.ttl_s == DEFAULT_TTL_S
        assert d.reason == "deterministic_opt_in"


# ============================================================
# 3. SemanticCache 端到端
# ============================================================
class TestSemanticCache:
    def test_judgment_not_stored(self):
        c = SemanticCache()
        req = _req("评分请求", cache_class=CacheClass.JUDGMENT.value)
        c.store(req, _resp())
        assert c.lookup(req) is None, "判定类结果不得被复用"

    def test_deterministic_hits(self):
        c = SemanticCache()
        req = _req("确定性抽取", cache_class=CacheClass.DETERMINISTIC.value)
        c.store(req, _resp('{"a": 1}'))
        got = c.lookup(req)
        assert got is not None
        assert got.text == '{"a": 1}'

    def test_hit_marks_cache_hit(self):
        """命中必须留痕——旧实现里命中与真实调用在 trace 中完全同形。"""
        c = SemanticCache()
        req = _req("确定性抽取", cache_class=CacheClass.DETERMINISTIC.value)
        c.store(req, _resp())
        got = c.lookup(req)
        assert got is not None
        assert got.cache_hit is True
        assert got.cache_key == decide(req).key

    def test_five_dimensions_not_shared(self):
        """端到端复现事故场景：五维各自独立，不共用响应。"""
        c = SemanticCache()
        body = "正文" * 4000
        dims = ["人设稳定性", "设定一致性", "逻辑漏洞", "连贯性", "追读力"]
        reqs = [
            _req(f"请评估「{d}」维度。\n\n{body}",
                 cache_class=CacheClass.DETERMINISTIC.value)
            for d in dims
        ]
        # 事故中五个维度会被判定为"可缓存"（未声明）→ 第一份响应被后四次复用
        for i, r in enumerate(reqs):
            c.store(r, _resp(f'{{"value": {i + 1}}}'))
        texts = [c.lookup(r).text for r in reqs]  # type: ignore[union-attr]
        assert len(set(texts)) == 5, "五个维度必须拿到五份不同的响应"

    def test_ttl_expiry(self):
        c = SemanticCache(ttl_s=0.01)
        req = _req("x", cache_class=CacheClass.DETERMINISTIC.value)
        c.store(req, _resp())
        time.sleep(0.02)
        assert c.lookup(req) is None

    def test_eviction_at_max_size(self):
        c = SemanticCache(max_size=2)
        for i in range(3):
            r = _req(f"req-{i}", cache_class=CacheClass.DETERMINISTIC.value)
            c.store(r, _resp(f"resp-{i}"))
        assert len(c._cache) == 2
        # 最旧的 req-0 应已被淘汰
        assert c.lookup(_req("req-0", cache_class=CacheClass.DETERMINISTIC.value)) is None


# ============================================================
# 4. 契约：chat_utility 默认不缓存
# ============================================================
class TestChatUtilityContract:
    def test_utility_default_has_no_cache_class(self):
        """agent 侧契约：不传 cache_class 时 extra 中不得出现该键。"""
        import inspect

        from agent.client.gateway_adapter import chat_utility

        sig = inspect.signature(chat_utility)
        assert "cache_class" in sig.parameters, "chat_utility 必须暴露 cache_class 开关"
        assert sig.parameters["cache_class"].default is None, "默认必须是 None（不缓存）"
