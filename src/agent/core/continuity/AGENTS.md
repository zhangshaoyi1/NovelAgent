# AGENTS.md - core/continuity/ 连续性账本

## 职责

对标 DeepWrite `long-ledger/record.ts`，把长篇一致性从「写后检查」升级为「写前结构化事实账本 + 信息差 + 未闭环 + 章交接」，物化投影喂写手（G15 P0-1）。

## 核心模块

| 文件 | 导出 | 作用 |
|------|------|------|
| `models.py` | `ContinuityFact`, `ContinuityKnowledge`, `ContinuityOpenLoop`, `ContinuityHandoff`, `ContinuityLedger`, `ContinuityProj` | 数据模型 |
| `ledger.py` | `ContinuityLedgerStore` | 账本持久化（按章 commit 归档） |
| `projection.py` | `project`, `project_to_text` | 物化投影 |
| `derive.py` | `derive_open_loop_status`, `expected_resolve_chapter`, `is_overdue` | 开环状态/逾期推导 |

## 依赖规则

- 仅依赖 base（pydantic + validation）
- 不依赖上层，放置于引擎层 core/