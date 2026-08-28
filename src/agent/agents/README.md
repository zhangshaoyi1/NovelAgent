# agents/ — 多智能体团队

## 职责
封装独立可运行的 Agent 智能体，每个 Agent 负责一个明确的创作角色。

## 包含文件
| 文件 | 职责 |
|------|------|
| `planner.py` | PlannerAgent：架构师，产出 Master Plan |
| `editor.py` | EditorAgent：主编，一致性仲裁 |
| `evaluator.py` | EvaluatorAgent：评测员，全书"不崩"终审 + 自动回溯修复 |
| `writer_agent.py` | WriterAgent：自主写章 Agent（Writer + Critic 内联） |

## 依赖规则
- 依赖 core/ 各子包（story/, quality/, engine/, base/）
- 依赖 client/ (LLMClient)
- 不依赖 workflows/（工作流编排 Agent，而非被编排）

## 设计原则
- `run()` / `run_async()` 统一接口
- `decide` 函数可注入，便于离线测试
- LLM 不可用时优雅降级，不阻断