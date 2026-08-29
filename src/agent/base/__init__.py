"""基础抽象层

提供所有上层组件依赖的基础抽象：
- Agent 基类（统一接口）
- 消息协议定义
- 结果基类
- 公共类型定义
- 配置基类工具
- LLM 协议层（LLMError / LLMConfig / LLMResponse / LLMProvider 注册表）
- 纯工具函数（parse_llm_json / safe_remove / chunk_text）
- 结构化输出（pydantic_to_json_schema / extract_json / StructuredOutputError）

遵循依赖规则：base 不依赖任何上层（client/core/agents/workflows），所有上层依赖 base。
"""

from __future__ import annotations

from agent.base.agent import Agent
from agent.base.config import BaseConfig
from agent.base.llm import (
    LLMConfig,
    LLMError,
    LLMProvider,
    LLMResponse,
    register_provider,
)
from agent.base.message import Message, Role
from agent.base.result import AgentResult
from agent.base.types import (
    AsyncCallback,
    JsonDict,
    ModelName,
    TokenCount,
)
from agent.base.utils import chunk_text, parse_llm_json, safe_remove
from agent.base.structured_output import (
    StructuredOutputError,
    extract_json,
    pydantic_to_json_schema,
)

__all__ = [
    "Agent",
    "BaseConfig",
    "Message",
    "Role",
    "AgentResult",
    "AsyncCallback",
    "JsonDict",
    "ModelName",
    "TokenCount",
    # LLM 协议层
    "LLMConfig",
    "LLMError",
    "LLMProvider",
    "LLMResponse",
    "register_provider",
    # 工具函数
    "parse_llm_json",
    "safe_remove",
    "chunk_text",
    # 结构化输出
    "pydantic_to_json_schema",
    "extract_json",
    "StructuredOutputError",
]
