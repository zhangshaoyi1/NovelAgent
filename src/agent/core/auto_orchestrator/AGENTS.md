# AGENTS.md - core/auto_orchestrator/ 一键自动编排

## 职责

提供一键完成自动编排能力（Auto-orchestrator）。

## 核心模块

| 文件 | 导出 | 作用 |
|------|------|------|
| `planner.py` | `AutoPlanner`, `WritingPlan`, `PlanPhase` | 自动规划器 |
| `decider.py` | `Decider`, `Decision`, `ConflictResolver`, `InterventionMode` | 决策器 |
| `executor.py` | `Executor`, `ExecutionResult`, `ExecutionStatus` | 执行器 |
| `plan_adjuster.py` | `PlanAdjuster`, `AdjustReason`, `PlanAdjustment` | 计划调整器 |

## 依赖规则

- 依赖 base、client、story
- 通过延迟导入避免循环依赖