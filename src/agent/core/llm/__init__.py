"""LLM 基础设施层

职责：提供 LLM 调用的上层抽象与辅助能力。
- 预算计划：成本预算配置加载
- Embedding：文本嵌入提供方（OpenAI 兼容 / Ollama / Qwen 本地）与路由选择
- 向后兼容层：model_routing / llm_client 废弃适配器（不再在此导出）

注意：核心 LLM 客户端实现位于 agent/client/，本包仅提供上层辅助设施。
"""

from agent.core.llm.budget_plan import load_budget_plan
from agent.core.llm.embedding_router import get_embedding_provider
from agent.core.llm.embeddings import (
    EmbeddingProvider,
    OllamaEmbedding,
    OpenAICompatibleEmbedding,
    QwenLocalEmbedding,
)

__all__ = [
    "load_budget_plan",
    "get_embedding_provider",
    "EmbeddingProvider",
    "OllamaEmbedding",
    "OpenAICompatibleEmbedding",
    "QwenLocalEmbedding",
]