"""LLM 基础设施层

职责：提供 LLM 调用的上层抽象与辅助能力。
- 预算计划：成本预算配置加载
- Embedding：文本嵌入提供方（OpenAI 兼容 / Ollama / Qwen 本地）与路由选择
- 向后兼容层：model_routing / llm_client 废弃适配器（不再在此导出）

下沉说明（2026-08-29）：embedding 实现与路由已下沉至 agent/client/（client/embeddings.py、
client/embedding_router.py），本包仅做再导出以保持 ``core.llm`` 公共 API 不变。
核心 LLM 客户端实现位于 agent/client/，本包仅提供上层辅助设施。
"""

from agent.client.embedding_router import get_embedding_provider
from agent.client.embeddings import (
    EmbeddingProvider,
    OllamaEmbedding,
    OpenAICompatibleEmbedding,
    QwenLocalEmbedding,
)
from agent.core.llm.budget_plan import load_budget_plan

__all__ = [
    "load_budget_plan",
    "get_embedding_provider",
    "EmbeddingProvider",
    "OllamaEmbedding",
    "OpenAICompatibleEmbedding",
    "QwenLocalEmbedding",
]