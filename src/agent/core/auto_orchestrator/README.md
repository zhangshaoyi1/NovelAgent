# auto_orchestrator/ — 自动编排

## 职责
一键完成从设定到完结的全流程自动编排。

## 包含文件
| 文件 | 职责 |
|------|------|
| `planner.py` | 自动编排规划器（AutoPlanner） |
| `decider.py` | 自动决策器（Decider） |
| `executor.py` | 自动执行器（Executor） |
| `plan_adjuster.py` | 计划调整器（PlanAdjuster） |

## 依赖规则
- 依赖 engine/、story/、quality/

## 被依赖
- 一键完成入口