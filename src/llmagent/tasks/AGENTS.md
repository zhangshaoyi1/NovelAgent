# AGENTS.md - llmagent/tasks/ 业务 Task 定义

## 职责

业务 Task 定义，只写 `_execute()` + 声明 `TaskSpec`。

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

