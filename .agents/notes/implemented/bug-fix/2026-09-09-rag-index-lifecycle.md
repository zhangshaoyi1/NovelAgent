# Agent Note: RAG 索引生命周期闭环（自举/幂等 upsert/回滚同步/doctor 陈旧度）
Status: implemented

## Problem

「鸡生蛋」死锁：两侧均以「`.state/rag` 目录存在」作为是否建索引的开关，而建该目录的唯一途径是手动
跑 reindex——从未执行过 reindex 的项目永远零召回零索引。五灵破归档 70 章零召回事故即源于此：
目录不存在 ⇒ 不建索引 ⇒ 无召回 ⇒ 没人想到要建索引。

## Decision

涉及 `core/rag/indexer.py`、`retriever.py`、`vector_store.py`、`bm25.py`：

- **① 自举**：落盘后 `Indexer.ensure()`：索引为空则全量重建一次，落 `.bootstrapped` marker 防抖
  （避免每章重复全量重建），失败不阻断写章——写章成功优先于索引完整。
- **② 增量幂等 upsert**：`index_chapter` 写前先删同章旧切片，重写同章不留旧版切片，且 M5 与 agentic
  两条路径都接；修掉了 `agentic_write._maybe_index` 里 `hasattr` 的死分支（此前 agentic 路径恒不建
  索引，漏掉整条生产链路）。
- **③ 删除同步**：`m10_rollback` 归档后 `_sync_rag_index()` 清被回滚章节切片，避免召回已判废旧版正文
  （幽灵召回），失败只告警不阻断回滚——回滚是用户止损动作，索引不一致远比阻断回滚可接受。
- **④ 全量**：CLI `reindex` / Web `/api/rag/reindex`，用于目录缺失或需整体刷新的场景。
- **⑤ 体检**：`core/infra/doctor._check_rag` 按 `stats()` 报覆盖章数缺口 / 失效 source 数 / 更新时间，
  超阈值 warn 并指向 reindex（`drop_chapters()` / `remove_where()` / `reset()` / `reindex_all()` 是
  配套的删除后重建能力，供 doctor 建议与 CLI 直接调用）。

## Alternatives considered

- **保持手动 reindex 为唯一入口**：死锁无解，新项目零召回归复现——否决，必须自举。
- **回滚同步失败即阻断回滚**：回滚是用户止损动作，索引不一致远优于阻断回滚，故失败只告警。
- **agentic 路径沿用 `hasattr` 死分支**：原分支恒为假，agentic 永不索引，必须删死分支并接 `ensure()`。

## Consequences

- 新项目首次写章即自动建索引（自举）；回滚章节不再幽灵召回旧正文；doctor 可暴露索引陈旧度引导 reindex。
- `.state/rag` 从「手动开关」变为「自举产物」，运维心智模型改变——勿再依赖其存在与否判断是否需要索引；
  缺目录时直接写章即可，无需先手跑 reindex。
- 增量 upsert 的同章删旧重写保证了「改章后旧切片不残留」，向量库与 bm25 两侧都接该路径，避免双源不一致。
- `Indexer.ensure()` 在每次 `index_chapter` 前调用，幂等（有 `.bootstrapped` marker 则跳过全量重建）。
- agentic 路径修复点：`agentic_write._maybe_index` 删掉 `hasattr` 死分支后改走 `ensure()` +
  `index_chapter`，生产链路自此真正建索引（此前 M5 路径独享）。
- 幽灵召回根因：回滚只动 `running/` 归档、不碰向量库，故必须 `_sync_rag_index()` 显式清切片。
- 全量入口：`agent rag reindex`（CLI）与 `/api/rag/reindex`（Web）复用同一 `reindex_all()`，
  二者行为一致，避免 Web 与 CLI 重建结果分叉。
- `.state/rag` 自举产物语义：存在 `.bootstrapped` ⇒ 已至少全量建过一次；不存在 ⇒ 待首次写章自举，
  运维不应再据「目录是否存在」判断「是否需要索引」。
- 失败不阻断写章的边界：`ensure()` 异常被吞并向 doctor 暴露，写章流程继续——牺牲召回完整性换可用性，
  属有意权衡；索引缺口由 doctor warn 显式提示而非静默。
- bm25 与向量库是否各自维护 marker 还是共享单 marker，**待核实**（影响「半建」态诊断）。
- `reindex` 为幂等操作，重复执行安全；doctor 建议即指向它，无需手动清 `.state/rag` 再跑。
- 自举失败（如向量库异常）仅告警、不阻断写章，故「写章成功但零召回」仍可能因索引建失败发生，需 doctor
  复查 `stats()` 确认（**待核实**：自举失败的具体可观测信号是否足够醒目，建议在 crash.log 之外单列 rag 自举失败计数）。

## 运维与 doctor 字段

- `stats()` 三项：`covered_chapters`（覆盖章数）、`stale_sources`（失效 source 数）、`updated_at`（更新时间）。
- 阈值：覆盖章数缺口超阈值 / 失效 source 数 > 0 / `updated_at` 过旧 → doctor warn 并指向 reindex。
- `.bootstrapped` marker 仅在首次全量重建成功后落盘；重建中途失败不落 marker，下次写章会重试。
- 重建能力：`drop_chapters()`（按章删）、`remove_where()`（按条件删）、`reset()`（全清）、
  `reindex_all()`（全量重建）——doctor 建议与 CLI 直接调用同一组底层能力。
- 相关 commit：`793003b`。
