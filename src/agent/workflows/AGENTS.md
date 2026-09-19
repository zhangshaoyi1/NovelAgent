# AGENTS.md - workflows/ 工作流模块（按域组织）

## 职责

每个功能模块对应一个工作流文件，由 WorkflowOrchestrator 编排。

## 目录结构（2026-09-05 起为唯一结构）

| 子目录 | 阶段 | 文件 |
|--------|------|------|
| `planning/` | 写作规划（M1-M4） | `m1_config.py` / `m2_discuss.py` / `m3_outline.py` / `m4_character.py` |
| `writing/` | 章节写作（M5-M6, M8） | `m5_write_chapter.py`（主文件经多继承组合 Mixin：`m5_context.py` 上下文装配 / `m5_quality_gate.py` 质量闸 / `m5_persist.py` 落盘归档 / `m5_text_hygiene.py` 文本净化）+ `m6_adjust.py` / `m8_mode.py` / `agentic_write.py`；`writing/__init__.py` 只显式导出 agentic_write/m5_write_chapter/m6/m8，不含 m5 子模块 |
| `evaluation/` | 评测审计（M10-M21） | `m10_rollback.py` / `m11_export.py` / `m12_audit.py` / `m13_foreshadow.py` / `m14_architecture.py` / `m15_bookworm.py` / `m16_pacing.py` / `m17_learn.py` / `m18_recovery.py` / `m19_review_sync.py` / `m20_analyze.py` / `m21_review.py` |
| `market/` | 市场分析（M22-M23） | `m22_setup.py` / `m23_short.py` |
| `pipeline/` | 流水线编排（已拆多模块） | `agentic_pipeline.py`（主编排，含滚动体检检查点 `_rolling_eval_checkpoint`）+ 拆分件：`agentic_pipeline_agents/cost/ending/events/planning/types.py`、`mainline_orchestrator.py` / `mainline.py` / `budget_planner.py` / `qa_sync.py` / `plan_consistency.py` |

### 近期新增机制（2026-09-10~12）

- 滚动体检检查点（B1）与体检触发修复两件套（`agentic_pipeline.py`）
- 伏笔播种闭环（`m13_foreshadow.py` 接线章后归档 hook，P1-4）
- 回退预算跨批持久化（`rollback_budget.py`）
- 桥段禁用清单（`beat_sketch.py`，回滚率削减 P0）

### 规划监理链（M1–M6，2026-09-18~19）

| 里程碑 | 落点 |
|---|---|
| M1 + Fix | 章级强度档位供给（规划端独立小节 + 行首字段）；采样闸 `plan_gate_pace_tier.jsonl`，**恒不阻断** |
| M3 | 写手侧：7 条平权规则按档位分叉（`prompts/m5/pace_rules.md` + `writer_agent._writer_base(pace_relaxed)`） |
| M4 | 评委侧：`pace_tiers_of_window` 逐章供档，**只进评委端** |
| M5 | 规划评委（采样模式，**恒不阻断**） |
| M6 | 章后 hook `m5_persist._record_tension`，唯一生产入口在 `agentic_write.py`；纯**观测面**，失败降级不阻断 |

> ⚠ **M6：写章链路新增章后 hook 时，必须同时登记 `degrade_registry.py`**（`m5.record_tension` 已登记），
> 且观测面的失败**只降级不阻断**——不得让统计类逻辑影响本章产出。
> ⚠ **档位（意图）与张力（事实）是两条独立线**，禁止在写章链路上用张力反推/覆盖档位。

> ⚠️ 禁止在 `workflows/` 根目录新增平铺 .py 文件——一律放入对应域子包。

## 注册机制

- 所有工作流通过 `@workflow` 装饰器自动注册到 `WorkflowRegistry`
- 各子包 `__init__.py` 显式导出本包全部模块（触发装饰器注册）
- `workflows/__init__.py` 只导入五子包，不再全量扫描目录
- 消费者通过 `get_workflow(id)` 查询，无需硬编码注册表

## 依赖规则

- `workflows/` 依赖所有下层（base/client/core/agents）
- 子包间相互引用必须延迟导入（方法内 import），禁止顶部循环依赖
