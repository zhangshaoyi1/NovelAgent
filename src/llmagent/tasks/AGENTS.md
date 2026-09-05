# AGENTS.md - llmagent/tasks/ 业务 Task 定义

## 职责

业务 Task 定义，只写 `_execute()` + 声明 `TaskSpec`。

> **角色边界（2026-09-05）**：本目录下的 Task 是**内核验收/示例任务**——用于 M0–M3 里程碑测试（`llmagent_tests/`）和 `llmagent.app.LLMApp` 演示链路，证明七统一门禁在真实 Task 上生效。
> **它们不是生产实现，不要在此新增生产业务 Task。** 生产侧的小说流水线（写章/大纲/分析等）位于 `agent/src/agent/workflows/`，经 `agent/src/agent/tasks/task_registry.py` 包装为 `TaskSpec` 注册进 Catalog 后运行在同一个内核上。两处职责不同，不构成重复实现。

## 现有 Task

| 文件                 | 作用      |
| ------------------ | ------- |
| `analyze.py`       | 分析 Task |
| `outline.py`       | 大纲 Task |
| `review.py`        | 评审 Task |
| `write_chapter.py` | 写章 Task |

## 约束

- 禁止 `import gateway.providers` 或 `secrets`

- 每个 Task 文件只定义一个 TaskSpec 和对应的 Executor

