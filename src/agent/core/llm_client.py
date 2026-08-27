"""LLM 抽象层（向后兼容适配器）

⚠ 此文件仅用于向后兼容，新代码应直接使用 ``from agent.client import LLMClient``。

所有实现已迁移到 ``agent/client/`` 目录。
"""

from __future__ import annotations

import warnings

warnings.warn(
    "agent.core.llm_client 已废弃，请使用 agent.client 替代。",
    DeprecationWarning,
    stacklevel=2,
)

from agent.client import LLMClient, LLMConfig, LLMResponse, LLMProvider, OpenAIProvider, OllamaProvider  # noqa: F401, E501

__all__ = [
    "LLMClient",
    "LLMConfig",
    "LLMResponse",
    "LLMProvider",
    "OpenAIProvider",
    "OllamaProvider",
]