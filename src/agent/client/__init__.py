"""统一 LLM 客户端层

职责：提供所有 LLM 调用的统一入口，屏蔽不同 LLM 提供商差异。

分层说明（2026-09-03 重构后）：
- provider.py: 具体 Provider 实现（OpenAI/Ollama）
- client.py: 统一 LLM 客户端（对外暴露的唯一入口，内部使用原生 Gateway）
- gateway_adapter.py: Gateway 原生工厂与辅助函数（create_gateway / chat_creative 等）
- router.py: 动态模型路由（成功率熔断 + 回退）
- embeddings.py / embedding_router.py: 文本嵌入能力（自 core/llm 下沉至本层）
- LLM 协议类型（LLMConfig/LLMResponse/LLMError/LLMProvider/register_provider）
  已下沉至 agent.base.llm，本包负责再导出（向后兼容）。

使用方式：
    from agent.client import LLMClient, LLMConfig

    client = LLMClient()
    resp = client.chat_creative([{"role": "user", "content": "..."}])

依赖规则：
    client 依赖 base（消息协议、类型定义、LLM 协议），不依赖任何上层模块。
"""

from __future__ import annotations

from urllib.error import URLError

from agent.base.llm import (
    LLMConfig,
    LLMError,
    LLMProvider,
    LLMResponse,
    register_provider,
)
from agent.client.client import LLMClient, set_llm_event_hook
from agent.client.embeddings import (
    EmbeddingProvider,
    OllamaEmbedding,
    OpenAICompatibleEmbedding,
    QwenLocalEmbedding,
)
from agent.client.embedding_router import get_embedding_provider
from agent.client.provider import OpenAIProvider, OllamaProvider
from agent.client.router import ModelRouter, RouteCandidate, RouteDecision

__all__ = [
    "LLMClient",
    "set_llm_event_hook",
    "LLMConfig",
    "LLMError",
    "LLMProvider",
    "LLMResponse",
    "OpenAIProvider",
    "OllamaProvider",
    "register_provider",
    "ModelRouter",
    "RouteCandidate",
    "RouteDecision",
    "get_embedding_provider",
    "EmbeddingProvider",
    "OpenAICompatibleEmbedding",
    "OllamaEmbedding",
    "QwenLocalEmbedding",
    "URLError",
]
