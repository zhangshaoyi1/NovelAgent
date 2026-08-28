"""Indexer 测试（增量 A / T02）

覆盖：reindex 全量建索引、index_chapter 增量追加、embed 失败统计与 BM25-only 降级。
附：reindex 命令的 CLI 集成（注入假 embedder，验证命令注册 + JSON 信封）。
"""

from __future__ import annotations

import json
from pathlib import Path

from agent.cli import app
from agent.core.rag.indexer import Indexer
from agent.core.rag.vector_store import LocalVectorStore
from typer.testing import CliRunner

from tests.conftest import FakeEmbedder, make_project


class _NullEmbedder:
    """embed 始终返回空（模拟 embedding 不可达）"""

    def embed(self, texts: list[str]) -> list[list[float]]:
        return []


class TestIndexer:
    def test_reindex_builds_index(self, tmp_path: Path) -> None:
        d = make_project(tmp_path, n_chapters=3)
        stats = Indexer(d, embedder=FakeEmbedder()).reindex()
        assert stats["indexed_chunks"] > 0
        assert stats["chapters"] == 3
        assert stats["embedding_failed"] == 0
        assert (d / ".state" / "rag" / "index.json").exists()

    def test_reindex_counts_all_products(self, tmp_path: Path) -> None:
        d = make_project(tmp_path, n_chapters=2)
        stats = Indexer(d, embedder=FakeEmbedder()).reindex()
        # 至少应包含 world/outline/characters/relations/foreshadows/chapters 的切片
        assert stats["indexed_chunks"] > 5

    def test_index_chapter_incremental_append(self, tmp_path: Path) -> None:
        d = make_project(tmp_path, n_chapters=2)
        idx = Indexer(d, embedder=FakeEmbedder())
        idx.reindex()
        before = len(idx.store.chunks)

        # 模拟写出新章节并增量索引
        ch_file = d / "chapters" / "ch003.md"
        ch_file.write_text(
            "# 第 3 章\n\n林寻 逃亡 推演 撕开缺口，追兵已至。",
            encoding="utf-8",
        )
        idx.index_chapter(ch_file, "林寻 逃亡 推演 撕开缺口，追兵已至。")

        # 重新加载，确认增量写入
        reload = Indexer(d, embedder=FakeEmbedder())
        assert len(reload.store.chunks) > before

    def test_reindex_embedding_failure_counts_and_degrades(self, tmp_path: Path) -> None:
        d = make_project(tmp_path, n_chapters=2)
        stats = Indexer(d, embedder=_NullEmbedder()).reindex()
        assert stats["embedding_failed"] == stats["indexed_chunks"]

        # 即使 embed 全失败，切片仍被 BM25 索引（store 非空）
        store = LocalVectorStore(d / ".state" / "rag" / "index.json")
        store.load()
        assert len(store.chunks) == stats["indexed_chunks"]
        # 所有切片无向量
        assert all(c.embedding is None for c in store.chunks)

    def test_reindex_idempotent(self, tmp_path: Path) -> None:
        d = make_project(tmp_path, n_chapters=2)
        Indexer(d, embedder=FakeEmbedder()).reindex()
        first = len(Indexer(d, embedder=FakeEmbedder()).store.chunks)
        Indexer(d, embedder=FakeEmbedder()).reindex()  # 再次重建应覆盖
        second = len(Indexer(d, embedder=FakeEmbedder()).store.chunks)
        assert first == second


class _FakeLLMClient:
    """假 LLMClient：embed 委托给 FakeEmbedder（绕开真实网络）"""

    def embed(self, texts: list[str]) -> list[list[float]]:
        return FakeEmbedder().embed(texts)


class TestReindexCommand:
    def test_reindex_cli_json(self, tmp_path: Path, monkeypatch) -> None:
        """reindex --json 经命令注册 + 注入假 embedder，输出合法信封"""
        d = make_project(tmp_path, n_chapters=3)
        # 注入假 embedder（命令内部 Indexer 默认 LLMClient → 由 patch 替换为假）
        monkeypatch.setattr(
            "agent.client.LLMClient", _FakeLLMClient
        )

        runner = CliRunner()
        result = runner.invoke(app, ["reindex", "--json", "-d", str(d)])
        assert result.exit_code == 0, result.output

        data = json.loads(result.stdout)
        assert data["success"] is True
        assert data["chapters"] == 3
        assert data["indexed_chunks"] > 0
        assert data["embedding_failed"] == 0
        assert (d / ".state" / "rag" / "index.json").exists()
