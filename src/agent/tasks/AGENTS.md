# AGENTS.md - tasks/ 任务模块

## 职责

提供原生 TaskSpec + Executor 模式的工作流注册与执行（Phase 4 重构）。

## 核心模块

| 文件 | 导出 | 作用 |
|------|------|------|
| `task_registry.py` | `TaskRegistry`, `create_task_spec` | 任务注册表与工厂函数 |

## 与旧 workflows/ 目录共存

- `workflows/`: 旧式 `@workflow` 装饰器 + `WorkflowOrchestrator`
- `tasks/`: 新式 `TaskSpec` + `Executor`（注册到 `llmagent.Catalog`）

## 依赖规则

- `tasks/` 是入口层，依赖所有下层
- 每个任务文件定义一个 TaskSpec 和对应的 Executor 实现