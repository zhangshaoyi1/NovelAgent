# AGENTS.md - llmagent/ 编排内核

## 职责

LLM 自主编排系统，提供 Task 运行时、状态机、七统一门面骨架、红线常量。

## 子包结构

| 子包 | 职责 |
|------|------|
| `gateway/` | 模型调用网关（唯一允许 import provider SDK，唯一读取 API key） |
| `kernel/` | 核心运行时（Task/Session/Agent/Planner/Memory/Failure） |
| `tasks/` | 业务 Task 定义（只写 _execute() + 声明 TaskSpec） |

## 依赖规则

- `kernel/` 不依赖 `gateway/`、`tasks/` 等上层模块
- `gateway/` 不依赖 `kernel/` 业务模块

## 设计文档

设计文档：`docs/orchestrator-design.md`（v1.10）