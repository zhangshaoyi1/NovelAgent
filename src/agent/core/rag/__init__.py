"""RAG 语义检索包（增量 A / T02）

统一导出供 ``agent.workflows`` / ``agent.cli.commands`` 复用：
- 类型：``Chunk`` / ``Hit``
- 存储：``VectorStore`` / ``LocalVectorStore``
- 兜底：``BM25Index``
- 引擎：``Indexer`` / ``Retriever``

嵌入能力已归位 ``llm/embeddings.py``（embedding 是模型调用能力），
Indexer 经 ``embed_fn`` 参数注入使用。
"""

from __future__ import annotations

from agent.core.rag._types import Chunk, Hit
from agent.core.rag.bm25 import BM25Index
from agent.core.rag.indexer import Indexer
from agent.core.rag.retriever import Retriever
from agent.core.rag.vector_store import LocalVectorStore, VectorStore

__all__ = [
    "Chunk",
    "Hit",
    "VectorStore",
    "LocalVectorStore",
    "BM25Index",
    "Indexer",
    "Retriever",
]
