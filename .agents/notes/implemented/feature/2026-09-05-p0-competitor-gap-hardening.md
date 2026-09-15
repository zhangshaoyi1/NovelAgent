# Agent Note: P0 竞品差距加固——缓存感知排序 / 受保护上下文 / 原子落盘 / 最简记忆包

Status: implemented

## Problem

`项目文档/竞品分析/竞品差距汇总.md` 确立 16 项差距，P0 四项直接影响长篇质量与可靠性：

1. 上下文装配未按 provider 端 prompt cache 命中率组织段序（AI-Novel-Writer 实践）。
2. 上下文压缩/裁剪不区分"权威事实 vs 可压缩历史"，可能静默压缩掉账本事实、伏笔硬约束（inkos protected/compressible 两层实践）。
3. 章节落盘用裸 `write_text`，进程中断会留下截断的半成品文件（inkos atomic-file-set 实践）。
4. 记忆取用无"不知道就会写错才保留"的裁剪判据，角色状态变更流水无条数上限（oh-story 最简记忆包实践）。

## Decision

按 `项目文档/竞品差距改进计划.md` 落地，四项全部纯业务层改动、不动分层与内核契约：

1. **P0-1** `core/infra/context_order.py`（新增）：`PromptSection(key, text, stability)` + `order_sections()` 按 stable→semi→volatile 稳定排序拼接；`m5_write_chapter._generate_chapter` 的 system 段全部改走该装配（不改任何段内容）。
2. **P0-2** `core/infra/context.py`：`ContextItem.protected` 标记 + `ProtectedContextOverflowError`（携带逐条 token 统计）；`compress()` 永不合并 protected 条目；`fit()` 无条件保留 protected，超预算抛错而非截断（上层可裁剪非受保护段后重试，符合"降级不阻断"）。
3. **P0-3** `core/infra/atomic.py`（新增）：`atomic_write_text/bytes`（temp+replace 单文件原子）与 `atomic_write_set`（多文件全成或全不成）；`m5_persist._save_chapter` 落盘改走原子写。`state_machine.save()` 此前已是 temp+replace；写序"先正文后状态指针"保证崩溃只会出现可恢复态（章节已存在、进度落后）。
4. **P0-4** `memory/memory_pack.py`（新增）：`classify_memory_type` + `minimal_memory_pack`（仅保留 current_state / causal_history / world_constraint 三类）+ `prune_recent_changes`（`MAX_RECENT_CHANGES=10`，超出合并为摘要行）；`MemoryLayer.recall_minimal()` 提供过滤后召回；`ConsolidatedMemory.update()` 对角色条目 changes 列表自动折叠；`prompts/m5/generate.md` 增第 16 条写作要求（判据语句进提示词，代码与提示词双处防漂移）。

## Alternatives considered

- **P0-2 改 llmagent 内核 ContextPolicy**：内核当前无压缩路径，业务侧 ContextEngine 是唯一压缩点；动内核契约会触发 redlines/架构测试连锁，收益为零。保持业务层实现，若未来内核引入压缩再按同一 protected 语义下沉。
- **P0-3 在 m5 run() 里把章节 + state.json 合并为一次 atomic_write_set**：需要在 RAG 增量索引前移动 `_update_progress`，改动主流程顺序，风险大于收益（崩溃窗口本就极小且方向安全）。采用"单文件原子 + 写序保证"，`atomic_write_set` 作为通用件留给多文件提交场景（如 P1-5 delta 结算）。
- **P0-4 在 m5_context 里对 characters/*.md 做结构化裁剪**：角色档案是自由 Markdown，无结构化变更列表可折叠；判据先行落在记忆层（有结构处），提示词层兜底语义。

## Consequences

- 写章 system prompt 段序变化：跨章生成的稳定前缀（系统规则/文风）可命中 provider prompt cache；`m5.generate` 渲染内容本身不变（user 段未动）。
- 新增异常类型 `ProtectedContextOverflowError`：调用方若直接吞异常需注意——设计意图是**显式失败**，只有上层降级策略可捕获重试。
- `generate.md` 新增第 16 条要求：`scripts/lint_prompts.py` 不受影响（该 key 无 legacy 兜底）；`verify_prompts_zero_regression.py` 不覆盖 m5.generate。
- 测试：`tests/test_context_order.py`（P0-1，先行落地）、`tests/test_protected_context.py`、`tests/test_atomic_write.py`、`tests/test_memory_pack.py`。
