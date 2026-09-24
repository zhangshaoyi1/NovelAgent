# Agent Note: 结算 delta 协议缺"新增剧情线"合法入口

- Status: implemented
- date: 2026-09-21
- lifecycile: /architecture/core-continuity-settle-protocol
- relates: .agents/notes/implemented/bug-fix/2026-09-11-cross-chapter-continuity-g15.md

## Problem

写章结算（M5 `ledger_delta`）运行中文书第 20 章触发：

```
[degrade] ledger_delta.apply：第20章账本 delta 校验失败（账本未被修改）
LedgerDeltaError('loop_op 目标不存在：loop_id=傀儡制作（op=resolve）。...')
```

追查发现这不是"模型幻觉"，而是**结算协议结构性缺口**：

1. `LoopOp` 预检强制：`loop_ops` 只能操作**已存在于账本 `open_loops`** 的 loop（`delta.py apply_ledger_delta` L118-126），未命中则整体抛错、账本分毫不动。
2. 但结算 delta 协议**没有登记"新开环"的字段**：`LedgerDelta` 仅 `facts/knowledge/loop_ops/handoff`（`extra="forbid"`）；`FactDomain` 枚举为 `character|relationship|world|plot|foreshadowing`，无 `open_loops`（`models.py L24`）；prompt schema（`ledger_delta.md` L24-37）同样无。
3. 报错文案却指引"请在 facts/open_loops 增量中显式给出完整条目"——**该通道在协议中不存在**，模型照做也过不了 schema。

结论：一条剧情线**从未被登记为开环，却想被 resolve**，在当下协议里是走不通的死局。真实"新增线"入口仅 `ContinuityLedger.commit(open_loops=[...])`（确定性路径），但那是另一套入口，LLM 结算侧无此能力。根因是协议欠设计，错误文案让模型走死路。

## Decision

给结算 delta 协议补齐"新增/登记开环"的合法通道，与"对已有线操作"形成二选一的明确铁律：

- `LedgerDelta` 增加 `open_loops: list[ContinuityOpenLoop] = []`（默认空）。
- `apply_ledger_delta` 调整执行顺序：**先合并 `delta.open_loops` 入账本**（同 `commit` 语义：同 id 覆盖、新增追加，`source_commit_id` 收口为本 commit），**再重建 `loop_index` 做 `loop_ops` 预检**——这样"同一 delta 里先登记新线、再 resolve 它"能自洽通过。
- prompt schema（`ledger_delta.md`）增加 `open_loops` 字段（对齐 `ContinuityOpenLoop` 域），并把结算铁律拆成二选一：**新增剧情线→填 `open_loops`；对已存在的线做 advance/resolve/defer/abandon→填 `loop_ops`**。
- 修掉报错文案：改为指向真实存在的 `delta.open_loops` 通道。

## Alternatives considered

- **A. 只改报错文案**（让提示词别再让模型走 `facts/open_loops`）：成本最低，但不解决"新增线"无能力问题，模型想 resolve 一条未登记线仍必然失败——不满足。
- **B. 允许 `loop_ops.resolve` 对不存在 id 隐式开环**：一行改动，但会绕过"先登记再闭环"的纪律，与 `open_loops` 账本的单据完整性原则冲突；且会让幻觉 loop_id 静默落库——驳回。
- **C. 增加 schema 字段 `open_loops` + 先合并后预检（正选）**：保持"不凭空、需显式登记"的纪律，同时给出一条自洽通路。成本适中，正向可测。

## Consequences

- **语义**：结算现在支持"本章新立一条剧情线"与"对既有线闭环/推进"；同一 delta 可先立线再操作。
- **不变式**：`open_loops` 仍必须显式给出完整条目（`detail`/`kind` 必填），`extra="forbid"` 仍拒绝多余字段。账本污染面不变，只是把原本"必然失败"的空隙补成合法路径。
- **回归面**：`apply_ledger_delta` 是所有结算的必经路径，新增默认空字段不改既有行为；schema 增加字段仅放宽输入允许集，不破坏既有 delta。
- **风险**：模型可能滥用"先立线再 resolve"自造闭环——由 prompt 铁律（宁缺毋滥、证据必须来自本章正文）与既有终态不回退状态机兜底。
- **待办**：proposed → implemented 迁移时同步 commits 号。

## References

- `agent/src/agent/core/continuity/delta.py`（`LedgerDelta`、`apply_ledger_delta` L118-126）
- `agent/src/agent/core/continuity/models.py`（`FactDomain` L24、`ContinuityOpenLoop` L69-84、`ContinuityLedger` open_loops 合并）
- `agent/src/agent/prompts/m5/ledger_delta.md`（schema L24-37、铁律 L15-22）
- `agent/src/agent/workflows/writing/ledger_delta_producer.py`（`_render_opening_state` L33-58、`produce_and_apply_delta`）

## 实施记录

| 日期 | 动作 | commit | 备注 |
|---|---|---|---|
| 2026-09-21 | 改码（Decision 全项）+ 报错文案修正 | agent@待回填 | `LedgerDelta.open_loops` 字段；`apply_ledger_delta` 预检前先合并 open_loops（同 id 覆盖/新增，`source_commit_id` 收口）；schema/铁律补 open_loops；报错改指真实通道 |
| 2026-09-21 | 测试 | 同文件 | `test_continuity_delta.py` 新增 5 条：同 delta 先登记再 resolve、open_loops 收口+去重、未登记仍须失败。受影响面 24 passed（delta+ledger），零回归 |