"""召回器（增量 A / T02）

``Retriever.retrieve(query, top_k)``：向量召回 + BM25 兜底融合，返回 ``list[Chunk]``。

设计要点：
- 查询向量由注入的 ``embedder``（默认 ``LLMClient``）生成；embed 失败则纯 BM25 召回。
- 融合策略：Reciprocal Rank Fusion（RRF，k=60），对向量/BM25 两套不同量纲的分数稳健。
- 索引缺失（``.state/rag/index.json`` 不存在）时返回空列表（调用方据此降级，不阻断写章）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.core.llm_client import LLMClient
from agent.core.rag._types import Chunk
from agent.core.rag.bm25 import BM25Index
from agent.core.rag.vector_store import LocalVectorStore

_RRF_K = 60.0


class Retriever:
    """RAG 召回器（读侧）"""

    def __init__(
        self,
        project_dir: Path,
        embedder: Any | None = None,
        store: LocalVectorStore | None = None,
        bm25: BM25Index | None = None,
    ) -> None:
        self.project_dir = Path(project_dir)
        self.rag_dir = self.project_dir / ".state" / "rag"
        self.embedder = embedder or LLMClient()
        self.store = store or LocalVectorStore(self.rag_dir / "index.json")
        self.bm25 = bm25 or BM25Index()
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self.store.load()
        # BM25 由已加载的 chunks 重建（无需独立持久化）
        if self.store.chunks:
            self.bm25.index(self.store.chunks)
        self._loaded = True

    def retrieve(self, query: str, top_k: int = 5) -> list[Chunk]:
        """语义召回：向量 + BM25 融合

        Args:
            query: 查询（通常为「第 N 章 + 支线 + 目标 + 阶段」）
            top_k: 返回条数

        Returns:
            融合后的 Chunk 列表（可能为空，调用方据此降级）。
        """
        self._ensure_loaded()
        if not self.store.chunks:
            return []

        # 1) 向量召回
        qvec: list[float] | None = None
        try:
            vectors = self.embedder.embed([query])
            qvec = vectors[0] if vectors else None
        except Exception:  # noqa: BLE001 - 向量失败时纯 BM25 兜底
            qvec = None
        vec_hits = self.store.search(qvec, top_k=top_k) if qvec else []

        # 2) BM25 兜底
        bm25_hits = self.bm25.search(query, top_k=top_k)

        return self._fuse(vec_hits, bm25_hits, top_k)

    @staticmethod
    def _fuse(vec_hits: list, bm25_hits: list, top_k: int) -> list[Chunk]:
        """RRF 融合两套召回结果（按 chunk 身份去重）"""
        scores: dict[tuple[str, int, str], float] = {}
        chunks: dict[tuple[str, int, str], Chunk] = {}

        def _key(c: Chunk) -> tuple[str, int, str]:
            return (c.source, c.chapter_num, c.text)

        for rank, hit in enumerate(vec_hits):
            key = _key(hit.chunk)
            scores[key] = scores.get(key, 0.0) + 1.0 / (_RRF_K + rank)
            chunks[key] = hit.chunk
        for rank, hit in enumerate(bm25_hits):
            key = _key(hit.chunk)
            scores[key] = scores.get(key, 0.0) + 1.0 / (_RRF_K + rank)
            chunks[key] = hit.chunk

        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        return [chunks[k] for k, _ in ranked[:top_k]]
