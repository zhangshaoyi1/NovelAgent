# AGENTS.md - core/rag/ RAG 语义检索包

## 职责

提供检索增强生成能力，供 workflows / cli 复用。

## 核心模块

| 文件 | 导出 | 作用 |
|------|------|------|
| `_types.py` | `Chunk`, `Hit` | 类型定义 |
| `vector_store.py` | `VectorStore`, `LocalVectorStore` | 向量存储 |
| `bm25.py` | `BM25Index` | 兜底 BM25 索引 |
| `indexer.py` | `Indexer` | 索引引擎 |
| `retriever.py` | `Retriever` | 检索引擎 |

## 设计说明

- 嵌入能力已归位 `llm/embeddings.py`（embedding 是模型调用能力）
- Indexer 经 `embed_fn` 参数注入使用