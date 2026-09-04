# AGENTS.md - workflows/ 工作流模块（按域组织）

## 职责

每个功能模块对应一个工作流文件，由 WorkflowOrchestrator 编排。

## 目录结构（2026-09-05 起为唯一结构）

| 子目录 | 阶段 | 文件 |
|--------|------|------|
| `planning/` | 写作规划（M1-M4） | `m1_config.py` / `m2_discuss.py` / `m3_outline.py` / `m4_character.py` |
| `writing/` | 章节写作（M5-M6, M8） | `m5_write_chapter.py` / `m6_adjust.py` / `m8_mode.py` / `agentic_write.py` |
| `evaluation/` | 评测审计（M10-M21） | `m10_rollback.py` / `m11_export.py` / `m12_audit.py` / `m13_foreshadow.py` / `m14_architecture.py` / `m15_bookworm.py` / `m16_pacing.py` / `m17_learn.py` / `m18_recovery.py` / `m19_review_sync.py` / `m20_analyze.py` / `m21_review.py` |
| `market/` | 市场分析（M22-M23） | `m22_setup.py` / `m23_short.py` |
| `pipeline/` | 流水线编排 | `agentic_pipeline.py` / `mainline_orchestrator.py` / `mainline.py` / `budget_planner.py` / `qa_sync.py` |

> ⚠️ 禁止在 `workflows/` 根目录新增平铺 .py 文件——一律放入对应域子包。

## 注册机制

- 所有工作流通过 `@workflow` 装饰器自动注册到 `WorkflowRegistry`
- 各子包 `__init__.py` 显式导出本包全部模块（触发装饰器注册）
- `workflows/__init__.py` 只导入五子包，不再全量扫描目录
- 消费者通过 `get_workflow(id)` 查询，无需硬编码注册表

## 依赖规则

- `workflows/` 依赖所有下层（base/client/core/agents）
- 子包间相互引用必须延迟导入（方法内 import），禁止顶部循环依赖
