# llm/ — LLM 基础设施层

## 职责
模型路由、预算规划、文本嵌入等 LLM 基础设施。

## 包含文件
| 文件 | 职责 |
|------|------|
| `budget_plan.py` | 预算规划（BudgetPlan, ChapterBudget - token 用量预估） |
| `llm_client.py` | CoreLLMClient（核心 LLM 客户端抽象） |
| `model_routing.py` | 模型路由（ModelRouter - 按用途选择模型） |
| `embeddings.py` | 文本嵌入提供方（EmbeddingProvider / OpenAI 兼容 / Ollama / Qwen 本地；从 rag/ 归位至此） |
| `embedding_router.py` | Embedding 路由（按配置选择 embedding 后端，client/ 经此获得能力） |

## 依赖规则
- 不依赖任何其他 core 子包（仅标准库 + openai/transformers 可选依赖）
- 不依赖业务层

## 被依赖
- client/ (LLMClient 包装、embedding)
- rag/ (Indexer 经 embed_fn 参数注入使用，实际类型来自本包)
- memory/ (SemanticMemory 向量后端)