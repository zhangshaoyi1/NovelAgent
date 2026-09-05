"""Memory Layer 基础抽象（Phase 2）

统一记忆三层（语义 / 会话 / 整合）的共享基元：

- ``MemoryEntry``：单条记忆（带类型、标签、来源，便于检索与追溯）。
- ``RetrievalScorer``：可插拔的离线检索打分器（默认 char-bigram 重叠，
  零依赖、零网络，保证测试与离线环境可用）；Phase 5 起 ``SemanticMemory``
  支持注入向量函数（复用 ``core/rag/embeddings``）启用真实向量检索，不可达
  时自动回退到本离线打分器。

设计原则（与项目"降级不阻断"一致）：
- 检索失败时返回空列表而不是抛异常。
- 持久化失败由调用方捕获，不阻断写作主流程。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


def _bigrams(text: str) -> dict[str, int]:
    """中文友好的 char-level bigram 词频。

    对中文按字符切 bigram；对 ASCII 单词按空白/标点切词，兼顾中西文。
    """
    counts: dict[str, int] = {}
    words = re.findall(r"[a-z0-9]+", text.lower())
    cjk = re.findall(r"[\u4e00-\u9fff]", text)
    for w in words:
        counts[w] = counts.get(w, 0) + 1
    for i in range(len(cjk) - 1):
        bg = cjk[i] + cjk[i + 1]
        counts[bg] = counts.get(bg, 0) + 1
    if not words and not cjk and text.strip():
        counts[text.strip()] = 1
    return counts


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    """两个词频字典的余弦相似度（无交集返回 0.0）。"""
    if not a or not b:
        return 0.0
    dot = sum(a.get(k, 0.0) * v for k, v in b.items())
    na = sum(v * v for v in a.values()) ** 0.5
    nb = sum(v * v for v in b.values()) ** 0.5
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class RetrievalScorer(Protocol):
    """可插拔检索打分器协议：query/candidate → 相似度 [0,1]"""

    def __call__(self, query: str, candidate: str) -> float: ...


def default_scorer(query: str, candidate: str) -> float:
    """默认离线打分器：char-bigram + 拉丁词 余弦相似度。"""
    return _cosine(_bigrams(query), _bigrams(candidate))


def make_scorer() -> Callable[[str, str], float]:
    """返回默认打分器（便于注入/替换）。"""
    return default_scorer


@dataclass
class MemoryEntry:
    """单条语义记忆"""

    type: str = "fact"
    text: str = ""
    id: str = field(default_factory=lambda: str(time.time_ns()))
    tags: list[str] = field(default_factory=list)
    source: str = ""
    created_at: float = field(default_factory=lambda: time.time())
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "text": self.text,
            "tags": self.tags,
            "source": self.source,
            "created_at": self.created_at,
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MemoryEntry":
        return cls(
            id=str(d.get("id", "")),
            type=str(d.get("type", "fact")),
            text=str(d.get("text", "")),
            tags=list(d.get("tags", []) or []),
            source=str(d.get("source", "")),
            created_at=float(d.get("created_at", 0.0)),
            meta=dict(d.get("meta", {}) or {}),
        )
