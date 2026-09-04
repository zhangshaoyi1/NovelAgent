# AGENTS.md - agents/ 多智能体团队

## 职责

四个核心智能体，负责小说创作的不同阶段。

## 核心模块

| 文件 | 导出 | 作用 |
|------|------|------|
| `planner.py` | `PlannerAgent`, `MasterPlan`, `Arc`, `CharacterSketch`, `PlannedForeshadow`, `QualityTargets` | 架构师（产出 Master Plan） |
| `writer_agent.py` | `WriterAgent` | 自主写章 Agent（Phase 1，Writer + Critic 内联） |
| `editor.py` | `EditorAgent`, `EditReport`, `EditConflict` | 主编 / 一致性仲裁 |
| `evaluator.py` | `EvaluatorAgent`, `NovelHealthReport`, `DimensionResult`, `RepairPlan` | 评测员（全书「不崩」终审 + 自动回溯修复） |

## 依赖规则

- `agents/` 依赖 `base/` + `client/` + `core/`
- 四个智能体之间相互独立，不互相依赖

## 重要约束

- 旧版文件名（`*_agent.py`）已废弃，新代码使用新文件名（无后缀）
- 新增智能体需在 `__init__.py` 中注册导出