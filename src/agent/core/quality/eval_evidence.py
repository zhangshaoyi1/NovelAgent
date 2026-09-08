"""评分证据（HA-Eval L3）

背景
----
原 ``DimensionResult`` 只有一个裸 ``value``，无从回答三个关键问题：

1. 这个分数是**真实调用**得来的，还是**复用了上一次响应**？
2. 它对应的 prompt 是哪一份（维度标签是否真的不同）？
3. 它**可信吗**？

2026-09-08 事故中，五个维度拿到同一份响应（缓存碰撞），却因为没有任何证据字段，
系统在"五个维度都给了 3.0"的情况下照单全收，直接触发了不可逆回滚。

设计
----
``EvalEvidence`` 为每个分数携带可溯源元数据 + 一个 ``confidence`` 字段。
:mod:`validators` 负责在分数**进入门禁之前**把坏数据挑出来并降级为
``confidence=0``；L4 处置层看到 ``confidence=0`` 一律不做任何处置动作。

依赖方向：本模块仅依赖标准库，不 import ``agent.agents.*``（避免与
DimensionResult 循环依赖——DimensionResult 以 ``Any`` 持有本类型实例）。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any


def hash_text(text: str) -> str:
    """稳定哈希（用于 prompt / 响应指纹）。"""
    return hashlib.sha256((text or "").encode("utf-8", "replace")).hexdigest()[:16]


@dataclass
class EvalEvidence:
    """单次维度评分的证据。"""

    # ---- 可溯源元数据 ----
    prompt_hash: str = ""        # 完整 messages 的哈希（维度标签不同 → 哈希不同）
    response_hash: str = ""      # LLM 原始响应哈希（复用同一响应 → 哈希相同）
    cache_hit: bool = False      # 是否命中语义缓存
    model: str = ""
    latency_ms: float = 0.0
    raw_excerpt: str = ""        # 原始响应前 500 字（供人工复盘）

    # ---- LLM 结构化输出 ----
    issues: list[dict[str, Any]] = field(default_factory=list)
    rationale: str = ""

    # ---- 可信度 ----
    confidence: float = 1.0      # 0.0 = 不可信，**禁止触发任何处置动作**
    validation: list[str] = field(default_factory=list)  # 校验失败原因

    @property
    def degraded(self) -> bool:
        return self.confidence <= 0.0

    def degrade(self, reason: str) -> None:
        """降级为不可信并记录原因（幂等）。"""
        self.confidence = 0.0
        if reason not in self.validation:
            self.validation.append(reason)

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt_hash": self.prompt_hash,
            "response_hash": self.response_hash,
            "cache_hit": self.cache_hit,
            "model": self.model,
            "latency_ms": round(self.latency_ms, 2),
            "raw_excerpt": self.raw_excerpt,
            "issues": list(self.issues),
            "rationale": self.rationale,
            "confidence": self.confidence,
            "validation": list(self.validation),
        }


def build_evidence(
    *,
    messages: list[dict[str, str]] | None = None,
    raw_response: str = "",
    cache_hit: bool = False,
    model: str = "",
    latency_ms: float = 0.0,
    issues: list[dict[str, Any]] | None = None,
    rationale: str = "",
) -> EvalEvidence:
    """便捷构造：自动计算 prompt / 响应指纹。"""
    prompt_blob = "\x1f".join(
        f"{m.get('role', '')}\x00{m.get('content', '')}" for m in (messages or [])
    )
    return EvalEvidence(
        prompt_hash=hash_text(prompt_blob),
        response_hash=hash_text(raw_response),
        cache_hit=cache_hit,
        model=model,
        latency_ms=latency_ms,
        raw_excerpt=(raw_response or "")[:500],
        issues=list(issues or []),
        rationale=rationale or "",
    )
