"""RAG 语义检索包（增量 A / T02）

统一导出供 ``agent.workflows`` / ``agent.cli.commands`` 复用：
- 类型：``Chunk`` / ``Hit``
- 嵌入：``EmbeddingProvider`` / ``OpenAICompatibleEmbedding`` / ``OllamaEmbedding``
- 存储：``VectorStore`` / ``LocalVectorStore``
- 兜底：``BM25Index``
- 引擎：``Indexer`` / ``Retriever``
"""

from __future__ import annotations

from agent.core.rag._types import Chunk, Hit
from agent.core.rag.bm25 import BM25Index
from agent.core.rag.embeddings import (
    EmbeddingProvider,
    OllamaEmbedding,
    OpenAICompatibleEmbedding,
)
from agent.core.rag.indexer import Indexer
from agent.core.rag.retriever import Retriever
from agent.core.rag.vector_store import LocalVectorStore, VectorStore

__all__ = [
    "Chunk",
    "Hit",
    "EmbeddingProvider",
    "OpenAICompatibleEmbedding",
    "OllamaEmbedding",
    "VectorStore",
    "LocalVectorStore",
    "BM25Index",
    "Indexer",
    "Retriever",
]
