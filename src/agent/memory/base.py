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
    # 中文/日文等连续字符 → bigram
    cjk = re.findall(r"[\u3000-\u9fff\uff00-\uffef]", text)
    for i in range(len(cjk) - 1):
        g = cjk[i] + cjk[i + 1]
        counts[g] = counts.get(g, 0) + 1
    # 拉丁词
    for word in re.findall(r"[A-Za-z0-9_]+", text.lower()):
        counts[word] = counts.get(word, 0) + 1
    return counts


def _cosine(a: dict[str, int], b: dict[str, int]) -> float:
    """两个词频字典的余弦相似度（无交集返回 0.0）。"""
    if not a or not b:
        return 0.0
    inter = set(a) & set(b)
    if not inter:
        return 0.0
    dot = sum(a[t] * b[t] for t in inter)
    na = sum(v * v for v in a.values()) ** 0.5
    nb = sum(v * v for v in b.values()) ** 0.5
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class RetrievalScorer(Protocol):
    """检索打分器协议：给定查询与候选文本，返回 [0,1] 相似度。"""

    def __call__(self, query: str, candidate: str) -> float: ...


def default_scorer(query: str, candidate: str) -> float:
    """默认离线打分器：char-bigram + 拉丁词 余弦相似度。"""
    return _cosine(_bigrams(query), _bigrams(candidate))


@dataclass
class MemoryEntry:
    """单条记忆。

    Attributes:
        id: 唯一标识（自动生成或指定）。
        type: 记忆类型，例如 fact / character / setting / event / plot_thread / decision。
        text: 记忆正文（语义内容）。
        tags: 标签（角色名、章节号、主题等），用于粗筛。
        source: 来源（章节号、\"plan\"、\"consolidated\" 等）。
        created_at: 写入时间戳（秒）。
        meta: 任意附加结构（如置信度、引用）。
    """

    type: str
    text: str
    id: str = ""
    tags: list[str] = field(default_factory=list)
    source: str = ""
    created_at: float = field(default_factory=lambda: time.time())
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "text": self.text,
            "tags": list(self.tags),
            "source": self.source,
            "created_at": self.created_at,
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MemoryEntry":
        return cls(
            type=str(d.get("type", "fact")),
            text=str(d.get("text", "")),
            id=str(d.get("id", "")),
            tags=list(d.get("tags", []) or []),
            source=str(d.get("source", "")),
            created_at=float(d.get("created_at", 0.0)),
            meta=dict(d.get("meta", {}) or {}),
        )


def make_scorer() -> Callable[[str, str], float]:
    """返回默认打分器（便于注入/替换）。"""
    return default_scorer
