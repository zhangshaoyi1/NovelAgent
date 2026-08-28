"""Embedding 路由

将 embedding provider 选择逻辑从 client/provider.py 上移到 core/llm/ 层，
保持 client/ 仅依赖 base/ 和 llm/ 的分层原则。
"""

from __future__ import annotations


def get_embedding_provider(config: object) -> object:
    """根据配置选择 embedding 后端

    Args:
        config: 包含 embedding_provider / provider / embedding_model / model /
                base_url / embedding_base_url / api_key / embedding_api_key 等属性的对象。

    Returns:
        Embedding provider 实例，具有 embed(texts) 方法。
    """
    from agent.core.rag.embeddings import (
        OllamaEmbedding,
        OpenAICompatibleEmbedding,
        QwenLocalEmbedding,
    )

    ep = (getattr(config, "embedding_provider", None) or getattr(config, "provider", "")).lower()

    if ep == "qwen_local":
        return QwenLocalEmbedding(
            model_name=getattr(config, "embedding_model", None) or "Qwen/Qwen2.5-0.5B-Instruct",
        )
    if ep == "ollama":
        return OllamaEmbedding(
            model=getattr(config, "embedding_model", None) or getattr(config, "model", ""),
            base_url=getattr(config, "base_url", None) or "http://localhost:11434",
        )
    return OpenAICompatibleEmbedding(
        model=getattr(config, "embedding_model", None) or getattr(config, "model", ""),
        base_url=getattr(config, "embedding_base_url", None) or getattr(config, "base_url", ""),
        api_key=getattr(config, "embedding_api_key", None) or getattr(config, "api_key", ""),
    )


__all__ = ["get_embedding_provider"]