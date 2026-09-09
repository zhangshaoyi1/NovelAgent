# AGENTS.md - core/rag/ RAG 语义检索包

## 职责

提供检索增强生成能力，供 workflows / cli 复用。

## 核心模块

| 文件 | 导出 | 作用 |
|------|------|------|
| `_types.py` | `Chunk`, `Hit` | 类型定义 |
| `vector_store.py` | `VectorStore`, `LocalVectorStore` | 向量存储（add / search / **remove_where / drop_chapters**） |
| `bm25.py` | `BM25Index` | 兜底 BM25 索引（index / **reset / reindex_all**） |
| `indexer.py` | `Indexer` | 索引引擎（reindex / index_chapter / **drop_chapters / ensure / stats**） |
| `retriever.py` | `Retriever` | 检索引擎（retrieve / retrieve_multi / **index_chapter / ensure_index**） |
| `_events.py` | `set_rag_event_hook`, `notify_rag_event` | 可注入事件 hook（不依赖 event_sourcing） |

## 设计说明

- 嵌入能力由 `agent.client.embedding_router.get_embedding_provider` 提供，
  经 `Indexer(project_dir, embedder=...)` / `Retriever(project_dir, embedder=...)`
  注入（默认走 `.env` 的 `_load_config_from_env()`，测试用 `FakeEmbedder`）。
- 索引持久化为 `.state/rag/index.json`（含 `updated_at`），BM25 不单独持久化，
  由 chunks 在加载/删除后重建。

## 索引生命周期（2026-09-09 修订）

| 环节 | 调用点 | 行为 |
|---|---|---|
| 自举 | `m5_write_chapter` 落盘后 `Indexer.ensure()` | 索引为空时全量重建**一次**（落 `.bootstrapped` marker 防抖） |
| 增量 | `m5_write_chapter` → `index_chapter()` | 写前先 `drop_chapters` 同章 → 幂等 upsert |
| 增量 | `agentic_write._maybe_index` → `Retriever.index_chapter()` | 同上（此前是 `hasattr` 死分支，已修复） |
| 删除 | `m10_rollback.rollback_to_chapter` → `_sync_rag_index()` | 清除被归档章节的切片，避免幽灵召回 |
| 全量 | CLI `reindex` / Web `/api/rag/reindex` | 丢弃旧索引重建（唯一自净手段） |
| 体检 | `core/infra/doctor._check_rag` | 按 `Indexer.stats()` 报覆盖章数 / 失效 source / 更新时间 |

**红线**：召回侧判据是 `index.json` 是否存在（不是目录）；任何索引操作失败都必须
降级不阻断写章/回滚。
