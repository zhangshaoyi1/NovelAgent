"""索引器（增量 A / T02）

``Indexer`` 负责把项目知识（world / sublines / characters / relations / foreshadows /
已写章节）切片、嵌入、写入本地向量库 + BM25 兜底索引。

- ``index_chapter(file, text)``：增量索引单章（M5 持久化后调用）。
- ``reindex()``：全量重建（``reindex`` 命令调用）。

设计要点：
- embed 委托给注入的 ``embedder``（默认 ``LLMClient``，复用 .env）；embed 失败返回空
  向量，检索降级为 BM25-only + 统计 ``embedding_failed``，**绝不阻断写章**。
- 索引持久化到 ``.state/rag/index.json``（由 ``LocalVectorStore`` 负责）。
- 损坏/缺失一律降级，不抛异常。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from agent.core.rag._types import Chunk
from agent.core.rag.bm25 import BM25Index
from agent.core.rag.vector_store import LocalVectorStore


class Indexer:
    """RAG 索引器（写侧）"""

    def __init__(
        self,
        project_dir: Path,
        embedder: Any | None = None,
    ) -> None:
        self.project_dir = Path(project_dir)
        self.rag_dir = self.project_dir / ".state" / "rag"
        self.embedder = embedder or self._default_embedder()
        self.store = LocalVectorStore(self.rag_dir / "index.json")
        self.bm25 = BM25Index()
        # 加载已有索引（增量 index_chapter 基于现有索引追加）
        self.store.load()
        if self.store.chunks:
            self.bm25.index(self.store.chunks)

    @staticmethod
    def _default_embedder() -> Any:
        from agent.client import LLMClient
        return LLMClient()

    # ============================================================
    # 切片
    # ============================================================
    def _chunk(self, text: str, source: str, chapter_num: int, kind: str) -> list[Chunk]:
        from agent.utils import chunk_text

        pieces = chunk_text(text, size=500)
        return [
            Chunk(text=p, source=source, chapter_num=chapter_num, kind=kind)
            for p in pieces
        ]

    def _read_and_chunk(
        self, path: Path, source: str, chapter_num: int, kind: str
    ) -> list[Chunk]:
        p = Path(path)
        if not p.exists():
            return []
        text = self._read_file_text(p)
        return self._chunk(text, source, chapter_num, kind)

    @staticmethod
    def _read_file_text(path: Path) -> str:
        """读取文件并剥离 frontmatter"""
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError:
            return ""
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                text = parts[2]
        return text

    # ============================================================
    # 嵌入 + 写入
    # ============================================================
    def _embed_and_add(self, chunks: list[Chunk]) -> int:
        """对切片做 embedding 并写入 store + bm25

        Returns:
            embedding 失败的切片数（向量为空即视为失败；失败切片仍进 BM25 兜底）。
        """
        failed = 0
        if not chunks:
            return failed
        try:
            vectors = self.embedder.embed([c.text for c in chunks])
        except Exception:  # noqa: BLE001 - embed 异常不应阻断索引
            vectors = []
        if len(vectors) != len(chunks):
            vectors = [None] * len(chunks)
        for c, v in zip(chunks, vectors):
            c.embedding = v if v else None
            if not c.embedding:
                failed += 1
        self.store.add(chunks)
        self.bm25.index(chunks)
        return failed

    # ============================================================
    # 公开 API
    # ============================================================
    def index_chapter(self, file: Path, text: str) -> None:
        """增量索引单章（M5 章节持久化后调用）

        Args:
            file: 章节文件路径（chNNN.md）
            text: 章节正文
        """
        f = Path(file)
        m = re.search(r"ch(\d+)", f.stem)
        chapter_num = int(m.group(1)) if m else 0
        try:
            source = str(f.relative_to(self.project_dir))
        except ValueError:
            source = f.name
        chunks = self._chunk(text, source, chapter_num, "chapter")
        self._embed_and_add(chunks)
        self.store.save()

    def reindex(self) -> dict[str, int]:
        """全量重建索引

        Returns:
            统计字典：indexed_chunks / embedding_failed / chapters
        """
        # 全新索引（丢弃旧索引，避免脏数据）
        self.store = LocalVectorStore(self.rag_dir / "index.json")
        self.bm25 = BM25Index()
        self.rag_dir.mkdir(parents=True, exist_ok=True)

        all_chunks: list[Chunk] = []
        # 设定类
        for name in ("world.md", "architecture.md", "outline.md"):
            all_chunks += self._read_and_chunk(
                self.project_dir / name, name, 0, "setting"
            )
        # 支线
        sub_dir = self.project_dir / "sublines"
        if sub_dir.exists():
            for f in sorted(sub_dir.glob("*/subline.md")):
                all_chunks += self._read_and_chunk(
                    f, str(f.relative_to(self.project_dir)), 0, "subline"
                )
        # 角色
        chars_dir = self.project_dir / "characters"
        if chars_dir.exists():
            for f in sorted(chars_dir.glob("*.md")):
                all_chunks += self._read_and_chunk(
                    f, str(f.relative_to(self.project_dir)), 0, "character"
                )
        # 关系网
        all_chunks += self._read_and_chunk(
            self.project_dir / "relations" / "graph.md", "relations/graph.md", 0, "relation"
        )
        # 伏笔
        all_chunks += self._read_and_chunk(
            self.project_dir / "foreshadows.md", "foreshadows.md", 0, "foreshadow"
        )
        # 已写章节
        chapter_count = 0
        chapters_dir = self.project_dir / "chapters"
        if chapters_dir.exists():
            for f in sorted(chapters_dir.glob("ch*.md")):
                m = re.search(r"ch(\d+)", f.stem)
                ch_num = int(m.group(1)) if m else 0
                all_chunks += self._read_and_chunk(
                    f, str(f.relative_to(self.project_dir)), ch_num, "chapter"
                )
                chapter_count += 1

        failed = self._embed_and_add(all_chunks)
        self.store.save()
        return {
            "indexed_chunks": len(all_chunks),
            "embedding_failed": failed,
            "chapters": chapter_count,
        }
