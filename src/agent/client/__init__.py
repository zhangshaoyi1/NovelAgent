"""统一 LLM 客户端层

职责：提供所有 LLM 调用的统一入口，屏蔽不同 LLM 提供商差异。

分层说明：
- config.py: LLM 配置模型（LLMConfig）
- provider.py: Provider 抽象 + OpenAI/Ollama 实现
- client.py: 统一 LLM 客户端（对外暴露的唯一入口）
- router.py: 动态模型路由（成功率熔断 + 回退）
- types.py: 响应类型

使用方式：
    from agent.client import LLMClient, LLMConfig

    client = LLMClient()
    resp = client.chat_creative([{"role": "user", "content": "..."}])

依赖规则：
    client 依赖 base（消息协议、类型定义），不依赖任何上层模块。
"""

from __future__ import annotations

from urllib.error import URLError

from agent.client.client import LLMClient
from agent.client.config import LLMConfig
from agent.client.provider import LLMProvider, OpenAIProvider, OllamaProvider, register_provider
from agent.client.router import ModelRouter, RouteCandidate, RouteDecision
from agent.client.types import LLMResponse

__all__ = [
    "LLMClient",
    "LLMConfig",
    "LLMProvider",
    "OpenAIProvider",
    "OllamaProvider",
    "ModelRouter",
    "RouteCandidate",
    "RouteDecision",
    "LLMResponse",
    "URLError",
]