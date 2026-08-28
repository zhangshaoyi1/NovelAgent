# llm/ — LLM 基础设施层

## 职责
模型路由、预算规划等 LLM 基础设施。

## 包含文件
| 文件 | 职责 |
|------|------|
| `budget_plan.py` | 预算规划（BudgetPlan, ChapterBudget - token 用量预估） |
| `llm_client.py` | CoreLLMClient（核心 LLM 客户端抽象） |
| `model_routing.py` | 模型路由（ModelRouter - 按用途选择模型） |

## 依赖规则
- 依赖 base/（异常定义）
- 不依赖业务层

## 被依赖
- client/ (LLMClient 包装)
- 所有需要 LLM 调用的上层