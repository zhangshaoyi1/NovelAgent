"""向量存储（增量 A / T02）

- ``VectorStore``：抽象基类（add / search / save / load）。
- ``LocalVectorStore``：默认零依赖纯 Python 余弦存储；检测到 ``numpy`` 时可选加速。
  持久化到 ``.state/rag/index.json``（向量内联 JSON，零依赖；可选 ``.npy`` 由 numpy 分支写入）。
  索引损坏（解析失败）时降级为空，绝不阻断写章。
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from agent.core.rag._types import Chunk, Hit


def _numpy_available() -> bool:
    """探测 numpy 是否可用（可选加速，非强制依赖）"""
    try:
        import numpy  # noqa: F401

        return True
    except ImportError:
        return False


class VectorStore(ABC):
    """向量存储抽象接口"""

    @abstractmethod
    def add(self, chunks: list[Chunk]) -> None:
        """写入一批 Chunk（含其 embedding）"""
        ...

    @abstractmethod
    def search(self, qvec: list[float], top_k: int) -> list[Hit]:
        """按查询向量返回 top_k 个最相似 Chunk（带分值）"""
        ...

    @abstractmethod
    def save(self) -> None:
        """持久化到磁盘"""
        ...

    @abstractmethod
    def load(self) -> None:
        """从磁盘加载（损坏则降级为空）"""
        ...


class LocalVectorStore(VectorStore):
    """纯 Python 余弦向量存储（零依赖）

    可选 numpy 加速：检测到 ``numpy`` 时，search 用矩阵运算；否则回退逐条计算。
    """

    def __init__(self, file: Path) -> None:
        self.file = Path(file)
        self.dim: int = 0
        self.chunks: list[Chunk] = []
        self._use_numpy = _numpy_available()
        self._matrix: "Any" = None  # numpy 数组缓存（懒构建）

    # ============================================================
    # 写入
    # ============================================================
    def add(self, chunks: list[Chunk]) -> None:
        for c in chunks:
            self.chunks.append(c)
            if c.embedding:
                self.dim = max(self.dim, len(c.embedding))
        # 维度变化，使 numpy 矩阵缓存失效
        self._matrix = None

    # ============================================================
    # 查询
    # ============================================================
    def search(self, qvec: list[float], top_k: int) -> list[Hit]:
        if not self.chunks or not qvec:
            return []
        if self._use_numpy:
            return self._search_numpy(qvec, top_k)
        return self._search_pure(qvec, top_k)

    def _search_pure(self, qvec: list[float], top_k: int) -> list[Hit]:
        scored: list[tuple[float, Chunk]] = []
        for c in self.chunks:
            if not c.embedding:
                continue
            s = self._cosine(qvec, c.embedding)
            scored.append((s, c))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [Hit(chunk=c, score=s) for s, c in scored[:top_k]]

    def _search_numpy(self, qvec: list[float], top_k: int) -> list[Hit]:
        import numpy as np

        if self._matrix is None:
            vecs = [c.embedding for c in self.chunks if c.embedding]
            self._matrix = np.array(vecs, dtype=float) if vecs else np.empty((0,))
            self._valid = [c for c in self.chunks if c.embedding]
        q = np.array(qvec, dtype=float)
        if self._matrix.size == 0:
            return []
        norms = np.linalg.norm(self._matrix, axis=1) * (np.linalg.norm(q) or 1.0)
        # 防御零向量，避免除零
        safe = np.where(norms == 0, 1e-9, norms)
        sims = self._matrix @ q / safe
        order = np.argsort(-sims)[:top_k]
        return [Hit(chunk=self._valid[int(i)], score=float(sims[int(i)])) for i in order]

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        """纯 Python 余弦相似度（带零向量防御）"""
        if not a or not b:
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(y * y for y in b) ** 0.5
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)

    # ============================================================
    # 持久化
    # ============================================================
    def save(self) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "dim": self.dim,
            "chunks": [
                {
                    "text": c.text,
                    "source": c.source,
                    "chapter_num": c.chapter_num,
                    "kind": c.kind,
                    "embedding": c.embedding or [],
                }
                for c in self.chunks
            ],
        }
        self.file.write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
        # 可选：numpy 分支额外写 .npy（加速下次加载），失败无所谓
        if self._use_numpy and self.chunks:
            try:
                import numpy as np

                vecs = [c.embedding for c in self.chunks if c.embedding]
                if vecs:
                    np.save(
                        str(self.file.with_suffix(".npy")),
                        np.array(vecs, dtype=float),
                    )
            except Exception:  # noqa: BLE001
                pass

    def load(self) -> None:
        if not self.file.exists():
            self.chunks = []
            self.dim = 0
            self._matrix = None
            return
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, ValueError):
            # 损坏降级为空，绝不阻断写章
            self.chunks = []
            self.dim = 0
            self._matrix = None
            return
        self.dim = int(data.get("dim", 0))
        raw_chunks = data.get("chunks", []) or []
        self.chunks = [
            Chunk(
                text=c.get("text", ""),
                source=c.get("source", ""),
                chapter_num=int(c.get("chapter_num", 0)),
                kind=c.get("kind", ""),
                embedding=(c.get("embedding") or None),
            )
            for c in raw_chunks
        ]
        self._matrix = None
