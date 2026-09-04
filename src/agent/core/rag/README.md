# rag/ — 检索增强生成

## 职责
为 LLM 提供语义参考的上下文召回能力，确保长篇小说写作中的设定一致性。

## 包含文件
| 文件 | 职责 |
|------|------|
| `_types.py` | 类型定义（Fragment, SearchResult） |
| `bm25.py` | BM25 检索器（BM25Retriever） |
| `indexer.py` | 索引器（Indexer - 自动索引设定、支线、角色、章节等；embedding 经 embed_fn 注入） |
| `retriever.py` | 检索器（Retriever - 混合检索 BM25 + 向量） |
| `vector_store.py` | 向量存储（VectorStore） |

**注**：embedding 提供方已归位 `llm/embeddings.py`（模型调用能力属 LLM 基础设施）。

## 依赖规则
- 依赖 base/、client/、story/
- 不依赖 engine/、quality/

## 被依赖
- workflows/ (M5 写章通过 RAG 注入语义参考)