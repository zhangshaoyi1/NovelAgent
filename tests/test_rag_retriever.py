"""BM25Index + Retriever 测试（增量 A / T02）

覆盖：BM25 中文关键词召回、Retriever 空索引返回空、向量+BM25 融合召回、
embed 失败纯 BM25 兜底召回。
"""

from __future__ import annotations

from pathlib import Path

from agent.core.rag._types import Chunk
from agent.core.rag.bm25 import BM25Index
from agent.core.rag.indexer import Indexer
from agent.core.rag.retriever import Retriever

from tests.conftest import FakeEmbedder, make_project


class _NullEmbedder:
    """embed 始终返回空（模拟 embedding 服务不可达）"""

    def embed(self, texts: list[str]) -> list[list[float]]:
        return []


class TestBM25Index:
    def test_search_returns_relevant(self) -> None:
        bm = BM25Index()
        bm.index(
            [
                Chunk(text="林寻 逃亡 推演 镜中", source="a", chapter_num=1, kind="chapter"),
                Chunk(text="天气 晴朗 吃饭 散步", source="b", chapter_num=2, kind="chapter"),
            ]
        )
        hits = bm.search("逃亡", top_k=1)
        assert hits, "应召回含『逃亡』的片段"
        assert "逃亡" in hits[0].chunk.text
        assert hits[0].score > 0

    def test_empty_query_returns_empty(self) -> None:
        bm = BM25Index()
        bm.index([Chunk(text="林寻 逃亡", source="a")])
        assert bm.search("   ", top_k=5) == []


class TestRetriever:
    def test_retrieve_empty_when_no_index(self, tmp_path: Path) -> None:
        d = make_project(tmp_path, n_chapters=3)
        # 未建立 .state/rag → 返回空，不触发任何网络
        assert Retriever(d, embedder=FakeEmbedder()).retrieve("anything") == []

    def test_retrieve_fuses_vector_and_bm25(self, tmp_path: Path) -> None:
        d = make_project(tmp_path, n_chapters=3)
        Indexer(d, embedder=FakeEmbedder()).reindex()
        retr = Retriever(d, embedder=FakeEmbedder())
        chunks = retr.retrieve("逃亡 推演", top_k=5)
        assert chunks, "向量+BM25 融合应召回相关片段"

    def test_retrieve_bm25_fallback_when_embed_fails(self, tmp_path: Path) -> None:
        d = make_project(tmp_path, n_chapters=3)
        # 用 NullEmbedder 建索引（embed 失败 → 仅 BM25）
        stats = Indexer(d, embedder=_NullEmbedder()).reindex()
        assert stats["embedding_failed"] == stats["indexed_chunks"]

        # 查询时 embed 同样失败，应纯 BM25 兜底召回
        retr = Retriever(d, embedder=_NullEmbedder())
        chunks = retr.retrieve("逃亡", top_k=5)
        assert chunks, "embed 失败时应由 BM25 兜底召回"

    def test_retrieve_relevant_chunk_present(self, tmp_path: Path) -> None:
        d = make_project(tmp_path, n_chapters=3)
        Indexer(d, embedder=FakeEmbedder()).reindex()
        retr = Retriever(d, embedder=FakeEmbedder())
        chunks = retr.retrieve("逃亡 推演", top_k=10)
        # ch1-3 含『逃亡』『推演』主题，BM25 兜底应召回相关切片
        assert any("逃亡" in c.text or "推演" in c.text for c in chunks)
