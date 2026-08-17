"""LocalVectorStore 测试（增量 A / T02）

覆盖：向量召回相似度排序、save/load 往返、损坏降级空、空库查询返回空。
"""

from __future__ import annotations

from pathlib import Path

from agent.core.rag._types import Chunk
from agent.core.rag.vector_store import LocalVectorStore


class TestLocalVectorStore:
    def test_search_returns_most_similar(self, tmp_path: Path) -> None:
        store = LocalVectorStore(tmp_path / "index.json")
        store.add(
            [
                Chunk(text="alpha beta", source="a", chapter_num=1, embedding=[1.0, 0.0]),
                Chunk(text="gamma delta", source="b", chapter_num=2, embedding=[0.0, 1.0]),
            ]
        )
        hits = store.search([1.0, 0.0], top_k=1)
        assert hits, "应返回命中"
        assert hits[0].chunk.text == "alpha beta"
        assert hits[0].score > 0.99

    def test_search_topk_limits(self, tmp_path: Path) -> None:
        store = LocalVectorStore(tmp_path / "index.json")
        store.add(
            [
                Chunk(text="x", embedding=[1.0, 0.0, 0.0]),
                Chunk(text="y", embedding=[0.0, 1.0, 0.0]),
                Chunk(text="z", embedding=[0.0, 0.0, 1.0]),
            ]
        )
        assert len(store.search([1.0, 0.0, 0.0], top_k=2)) == 2

    def test_save_load_roundtrip(self, tmp_path: Path) -> None:
        f = tmp_path / "index.json"
        store = LocalVectorStore(f)
        store.add([Chunk(text="hello", source="s", chapter_num=3, embedding=[0.2, 0.9])])
        store.save()

        store2 = LocalVectorStore(f)
        store2.load()
        assert len(store2.chunks) == 1
        assert store2.chunks[0].text == "hello"
        assert store2.chunks[0].chapter_num == 3
        assert store2.chunks[0].embedding == [0.2, 0.9]

    def test_load_corrupt_degrades_empty(self, tmp_path: Path) -> None:
        f = tmp_path / "index.json"
        f.write_text("{ this is not json", encoding="utf-8")
        store = LocalVectorStore(f)
        store.load()
        assert store.chunks == []

    def test_search_empty_store_returns_empty(self, tmp_path: Path) -> None:
        store = LocalVectorStore(tmp_path / "index.json")
        assert store.search([1.0, 0.0], top_k=5) == []

    def test_chunks_without_embedding_skipped_in_search(self, tmp_path: Path) -> None:
        store = LocalVectorStore(tmp_path / "index.json")
        store.add(
            [
                Chunk(text="has vec", embedding=[1.0, 0.0]),
                Chunk(text="no vec", embedding=None),  # BM25 场景：无向量
            ]
        )
        hits = store.search([1.0, 0.0], top_k=5)
        assert len(hits) == 1
        assert hits[0].chunk.text == "has vec"
