# rag/ — 检索增强生成

## 职责
为 LLM 提供语义参考的上下文召回能力，确保长篇小说写作中的设定一致性。

## 包含文件
| 文件 | 职责 |
|------|------|
| `_types.py` | 类型定义（`Chunk`, `Hit`） |
| `bm25.py` | BM25 兜底索引（`BM25Index`：`index` / `reset` / `reindex_all`） |
| `indexer.py` | 索引器（`Indexer`：`reindex` / `index_chapter` / `drop_chapters` / `ensure` / `stats`） |
| `retriever.py` | 检索器（`Retriever`：`retrieve` / `retrieve_multi` / `index_chapter` / `ensure_index`） |
| `vector_store.py` | 向量存储（`VectorStore` 抽象 + `LocalVectorStore` 纯 Python 实现） |
| `_events.py` | 可注入事件 hook（`set_rag_event_hook` / `notify_rag_event`） |

**注**：embedding 提供方是 `agent.client.embedding_router.get_embedding_provider`，
经 `embedder=` 参数注入（默认读 `.env`），测试用 `tests/conftest.FakeEmbedder`。

## 依赖规则
- 依赖 base/、client/、story/
- 不依赖 engine/、quality/

## 被依赖
- workflows/（M5 写章通过 RAG 注入语义参考；M10 回滚同步索引）

## 索引生命周期要点
- **自举**：写章落盘后 `Indexer.ensure()`，索引为空则全量重建一次（marker 防抖），
  修复「目录不存在 → 永不召回永不索引」的死锁。
- **幂等**：`index_chapter` 写前先删同章旧切片，重写/改稿不留旧版。
- **回滚联动**：`m10_rollback` 归档后调用 `drop_chapters`，清除已判废章节切片。
- **降级**：任何 embed/索引失败都不阻断写章与回滚。
