# AGENTS.md - workflows/ 工作流模块

## 职责

每个功能模块对应一个工作流文件，由 WorkflowOrchestrator 编排。

## 工作流文件

| 文件 | 作用 |
|------|------|
| `agentic_pipeline.py` | 自主写作主编排 |
| `agentic_write.py` | 唯一写章入口 |
| `budget_planner.py` | 预算规划 |
| `mainline.py` | 主流程编排 |
| `mainline_orchestrator.py` | 主流程协调器 |
| `qa_sync.py` | 质量同步 |
| `m1_config.py` | 项目配置 |
| `m2_discuss.py` | 讨论 |
| `m3_outline.py` | 大纲生成 |
| `m4_character.py` | 角色设计 |
| `m5_write_chapter.py` | 写章 |
| `m6_adjust.py` | 调整 |
| `m8_mode.py` | 模式切换 |
| `m10_rollback.py` | 回滚 |
| `m11_export.py` | 导出 |
| `m12_audit.py` | 审计 |
| `m13_foreshadow.py` | 伏笔管理 |
| `m14_architecture.py` | 架构确认 |
| `m15_bookworm.py` | 书虫评测 |
| `m16_pacing.py` | 节奏控制 |
| `m17_learn.py` | 学习 |
| `m18_recovery.py` | 恢复 |
| `m19_review_sync.py` | 评审同步 |
| `m20_analyze.py` | 分析 |
| `m21_review.py` | 评审 |
| `m22_setup.py` | 设置 |
| `m23_short.py` | 短篇写作 |

## 动态发现机制

- 所有工作流通过 `@workflow` 装饰器自动注册到 `WorkflowRegistry`
- 本模块动态导入所有工作流文件触发装饰器注册
- 消费者通过 `get_workflow(id)` 查询，无需硬编码注册表

## 依赖规则

- `workflows/` 依赖所有下层（base/client/core/agents）